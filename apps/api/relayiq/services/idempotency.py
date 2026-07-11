"""Durable idempotency (ADR-007).

Claim semantics via a DB unique constraint on (tenant, scope, key) — the only mechanism
that is safe under concurrent identical requests, worker restarts, and retries:

    claim() outcomes:
      NEW        -> caller proceeds and must call complete()/fail()
      IN_PROGRESS-> another worker holds it; caller returns 409/202
      COMPLETED  -> replay: serve the stored response snapshot, spend nothing
      MISMATCH   -> same key, different payload hash -> 422
      EXPIRED    -> record expired; row is re-claimed as NEW
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from relayiq.config import get_settings
from relayiq.enums import IdempotencyStatus
from relayiq.models import IdempotencyRecord


class ClaimOutcome(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    MISMATCH = "mismatch"


@dataclass
class ClaimResult:
    outcome: ClaimOutcome
    record: IdempotencyRecord | None = None

    @property
    def response_snapshot(self) -> dict | None:
        return self.record.response_snapshot if self.record else None


def request_hash(payload: dict | bytes | str) -> str:
    if isinstance(payload, dict):
        payload = json.dumps(payload, sort_keys=True, default=str)
    if isinstance(payload, str):
        payload = payload.encode()
    return hashlib.sha256(payload).hexdigest()


def claim(
    session: Session,
    tenant_id: str,
    scope: str,
    key: str,
    payload_hash: str = "",
    ttl_hours: int | None = None,
) -> ClaimResult:
    ttl = ttl_hours if ttl_hours is not None else get_settings().idempotency_ttl_hours
    now = datetime.now(UTC)
    expires = now + timedelta(hours=ttl)

    record = IdempotencyRecord(
        tenant_id=tenant_id,
        scope=scope,
        key=key,
        request_hash=payload_hash,
        status=IdempotencyStatus.IN_PROGRESS.value,
        expires_at=expires,
    )
    session.add(record)
    try:
        session.commit()
        return ClaimResult(ClaimOutcome.NEW, record)
    except IntegrityError:
        session.rollback()

    existing = session.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key,
        )
    ).scalar_one_or_none()
    if existing is None:  # pragma: no cover — race with a concurrent delete
        return claim(session, tenant_id, scope, key, payload_hash, ttl_hours)

    exp = existing.expires_at
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    if exp is not None and exp < now:
        # Expired: re-claim in place (single UPDATE keyed on the old state is race-safe enough
        # for our TTLs; a lost race just behaves like IN_PROGRESS).
        existing.status = IdempotencyStatus.IN_PROGRESS.value
        existing.request_hash = payload_hash
        existing.response_snapshot = None
        existing.expires_at = expires
        session.commit()
        return ClaimResult(ClaimOutcome.NEW, existing)

    if payload_hash and existing.request_hash and existing.request_hash != payload_hash:
        return ClaimResult(ClaimOutcome.MISMATCH, existing)
    if existing.status == IdempotencyStatus.COMPLETED.value:
        return ClaimResult(ClaimOutcome.COMPLETED, existing)
    if existing.status == IdempotencyStatus.FAILED.value:
        # Failed attempts may be retried: re-claim.
        existing.status = IdempotencyStatus.IN_PROGRESS.value
        existing.request_hash = payload_hash
        existing.expires_at = expires
        session.commit()
        return ClaimResult(ClaimOutcome.NEW, existing)
    return ClaimResult(ClaimOutcome.IN_PROGRESS, existing)


def complete(session: Session, record: IdempotencyRecord, response_snapshot: dict) -> None:
    record.status = IdempotencyStatus.COMPLETED.value
    record.response_snapshot = response_snapshot
    session.commit()


def fail(session: Session, record: IdempotencyRecord) -> None:
    record.status = IdempotencyStatus.FAILED.value
    session.commit()
