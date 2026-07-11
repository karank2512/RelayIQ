"""CRM synchronization: gate every field, then write eligible ones through the adapter.

Idempotent per (tenant, entity, value-set) via crm_sync_attempts.idempotency_key; retried
deliveries reuse the recorded attempt instead of writing twice.
"""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from relayiq.enums import EntityType, GateOutcome, ReviewTaskStatus, StalenessState, SyncStatus
from relayiq.models import (
    CanonicalFieldValue,
    CrmConnection,
    CrmSyncAttempt,
    ExternalIdentifier,
    ReviewTask,
)
from relayiq.observability.metrics import CRM_SYNCS
from relayiq.services import audit
from relayiq.services.crm import CrmAdapter, get_adapter, map_properties
from relayiq.services.crm_gate import FieldGateInput, gate_field, gate_summary


def get_connection(session: Session, tenant_id: str) -> CrmConnection | None:
    return session.execute(
        select(CrmConnection).where(
            CrmConnection.tenant_id == tenant_id, CrmConnection.is_active.is_(True)
        ).order_by(CrmConnection.created_at)
    ).scalars().first()


def _entity_lookup(entity_type: str, entity) -> dict:
    if entity_type == EntityType.CONTACT.value:
        return {"email": entity.work_email} if entity.work_email else {}
    return {"domain": entity.root_domain} if entity.root_domain else {}


def _reviewer_decisions(session: Session, tenant_id: str, entity_type: str, entity_id: str) -> dict[str, str]:
    rows = session.execute(
        select(ReviewTask).where(
            ReviewTask.tenant_id == tenant_id,
            ReviewTask.entity_type == entity_type,
            ReviewTask.entity_id == entity_id,
            ReviewTask.status.in_([
                ReviewTaskStatus.ACCEPTED.value, ReviewTaskStatus.OVERRIDDEN.value,
                ReviewTaskStatus.REJECTED.value,
            ]),
        )
    ).scalars().all()
    return {r.field_name: r.status for r in rows if r.field_name}


def sync_entity(
    session: Session,
    tenant_id: str,
    entity_type: str,
    entity,
    *,
    job_id: str | None = None,
    fields: list[str] | None = None,
    min_confidence: float = 0.6,
    crm_write_enabled: bool = True,
    dry_run: bool = False,
    trace_id: str | None = None,
    adapter: CrmAdapter | None = None,
) -> CrmSyncAttempt | None:
    connection = get_connection(session, tenant_id)
    if connection is None:
        return None
    adapter = adapter or get_adapter(connection.system, connection.mode)
    dry_run = dry_run or connection.mode == "dry_run"

    canon_rows = session.execute(
        select(CanonicalFieldValue).where(
            CanonicalFieldValue.tenant_id == tenant_id,
            CanonicalFieldValue.entity_type == entity_type,
            CanonicalFieldValue.entity_id == entity.id,
        )
    ).scalars().all()
    if fields:
        canon_rows = [r for r in canon_rows if r.field_name in fields]
    if not canon_rows:
        return None

    # Idempotency key over the exact value-set being synced.
    value_fingerprint = hashlib.sha256(
        json.dumps(
            {r.field_name: r.normalized_value or r.value for r in canon_rows}, sort_keys=True
        ).encode()
    ).hexdigest()[:24]
    idem_key = f"{entity_type}:{entity.id}:{value_fingerprint}"
    prior = session.execute(
        select(CrmSyncAttempt).where(
            CrmSyncAttempt.tenant_id == tenant_id, CrmSyncAttempt.idempotency_key == idem_key
        )
    ).scalar_one_or_none()
    if prior is not None and prior.status in (SyncStatus.SUCCESS.value, SyncStatus.SKIPPED.value):
        return prior  # identical value-set already synced — no duplicate write

    external = session.execute(
        select(ExternalIdentifier).where(
            ExternalIdentifier.tenant_id == tenant_id,
            ExternalIdentifier.entity_type == entity_type,
            ExternalIdentifier.entity_id == entity.id,
            ExternalIdentifier.system == connection.system,
        )
    ).scalars().first()
    crm_read = adapter.read(
        session, tenant_id, "contact" if entity_type == "contact" else "company",
        external.external_id if external else None, _entity_lookup(entity_type, entity),
    )
    crm_props = crm_read.properties or {}
    crm_stamps = crm_read.property_updated_at or {}
    reviewer = _reviewer_decisions(session, tenant_id, entity_type, entity.id)
    unresolved = {
        r.field_name
        for r in canon_rows
        if r.field_name in reviewer and reviewer[r.field_name] == ReviewTaskStatus.REJECTED.value
    }

    decisions = []
    field_changes: dict[str, dict] = {}
    mapped_now: dict[str, str] = {}
    for row in canon_rows:
        crm_prop_map = map_properties(entity_type, connection.system, {row.field_name: "x"})
        crm_prop = next(iter(crm_prop_map), None)
        crm_val = crm_props.get(crm_prop) if crm_prop else None
        stamp_raw = crm_stamps.get(crm_prop) if crm_prop else None
        stamp = None
        if stamp_raw:
            try:
                stamp = datetime.fromisoformat(stamp_raw)
            except ValueError:
                stamp = None
        state = StalenessState(row.staleness_state) if row.staleness_state else StalenessState.UNKNOWN
        d = gate_field(
            session, tenant_id, entity_type,
            FieldGateInput(
                field_name=row.field_name,
                new_value=row.normalized_value or row.value,
                confidence=row.confidence,
                has_unresolved_conflict=row.field_name in unresolved,
                reconciliation_outcome=None,
                staleness_state=state,
                reviewer_decision=reviewer.get(row.field_name),
                manually_locked=row.locked,
                crm_value=str(crm_val) if crm_val is not None else None,
                crm_value_updated_at=stamp,
            ),
            min_confidence=min_confidence,
            crm_write_enabled=crm_write_enabled,
        )
        decisions.append(d)
        field_changes[row.field_name] = {
            "before": crm_val,
            "after": row.normalized_value or row.value,
            "gate": d.outcome.value,
            "reasons": d.reasons,
        }
        if d.outcome == GateOutcome.WRITE:
            mapped_now.update(map_properties(entity_type, connection.system,
                                             {row.field_name: row.normalized_value or row.value}))
        elif d.outcome == GateOutcome.SECONDARY_PROPERTY:
            prop = map_properties(entity_type, connection.system,
                                  {row.field_name: row.normalized_value or row.value})
            mapped_now.update({f"relayiq_suggested_{k}": v for k, v in prop.items()})

    attempt = CrmSyncAttempt(
        tenant_id=tenant_id,
        connection_id=connection.id,
        job_id=job_id,
        entity_type=entity_type,
        entity_id=entity.id,
        external_id=crm_read.external_id,
        field_changes=field_changes,
        gate_summary=gate_summary(decisions),
        dry_run=dry_run,
        status=SyncStatus.PENDING.value,
        idempotency_key=idem_key,
        trace_id=trace_id,
    )
    session.add(attempt)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()  # concurrent identical sync: return the winner's attempt
        return session.execute(
            select(CrmSyncAttempt).where(
                CrmSyncAttempt.tenant_id == tenant_id, CrmSyncAttempt.idempotency_key == idem_key
            )
        ).scalar_one_or_none()

    if not mapped_now:
        attempt.status = SyncStatus.SKIPPED.value
        CRM_SYNCS.labels(system=connection.system, status="skipped").inc()
        session.flush()
        return attempt
    if dry_run:
        attempt.status = SyncStatus.SKIPPED.value
        attempt.gate_summary = {**attempt.gate_summary, "dry_run": True, "would_write": mapped_now}
        CRM_SYNCS.labels(system=connection.system, status="dry_run").inc()
        session.flush()
        return attempt

    result = adapter.write(
        session, tenant_id, "contact" if entity_type == "contact" else "company",
        crm_read.external_id, mapped_now,
    )
    if result.ok:
        attempt.status = SyncStatus.SUCCESS.value
        attempt.external_id = result.external_id
        attempt.synced_at = datetime.now(UTC)
        if external is None and result.external_id:
            session.add(ExternalIdentifier(
                tenant_id=tenant_id, entity_type=entity_type, entity_id=entity.id,
                system=connection.system, external_id=result.external_id,
            ))
    else:
        attempt.status = SyncStatus.RETRYING.value if result.retryable else SyncStatus.FAILED.value
        attempt.error = result.error
    CRM_SYNCS.labels(system=connection.system, status=attempt.status).inc()
    audit.record(
        session, tenant_id, action="crm.sync", object_type=entity_type, object_id=entity.id,
        after={"status": attempt.status, "fields": list(mapped_now)}, trace_id=trace_id,
    )
    session.flush()
    return attempt
