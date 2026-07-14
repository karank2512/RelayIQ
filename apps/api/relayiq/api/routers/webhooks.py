"""Inbound webhook endpoint with HMAC verification, replay protection, and delivery dedup.

Security order of operations (threat model §1–3): verify signature on the RAW body →
check timestamp replay window → dedupe delivery ID → only then parse/process. A replayed
delivery returns 200 with duplicate=true and spends nothing.
"""

import hashlib
import json

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from relayiq.config import get_settings
from relayiq.db import get_db
from relayiq.enums import JobStatus
from relayiq.logging_setup import get_logger
from relayiq.models import Account, Contact, EnrichmentJob, Tenant, WebhookDelivery
from relayiq.observability.metrics import WEBHOOKS
from relayiq.schemas.enrichment import WebhookEnrichmentPayload
from relayiq.services.entities import match_or_create_account, match_or_create_contact
from relayiq.services.webhook_security import verify_webhook

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])
log = get_logger("webhooks")


def _peek_tenant_slug(raw_body: bytes) -> str | None:
    """Extract tenant_slug from the (not-yet-authenticated) body ONLY to select which
    secrets verify the signature. Nothing else is read or acted on before verification."""
    try:
        doc = json.loads(raw_body)
        slug = doc.get("tenant_slug") if isinstance(doc, dict) else None
        return slug if isinstance(slug, str) and len(slug) <= 80 else None
    except (ValueError, TypeError):
        return None


@router.post("/enrichment")
async def enrichment_webhook(
    request: Request,
    response: Response,
    signature: str | None = Header(default=None, alias="X-RelayIQ-Signature"),
    delivery_id: str | None = Header(default=None, alias="X-Delivery-Id"),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    raw_body = await request.body()

    # Tenant-scoped secrets: when a tenant configures its own webhook secrets
    # (tenant.settings["webhook_secrets"]), ONLY those verify its deliveries — the
    # global secret cannot authorize enrichment for that tenant (threat model §5).
    slug = _peek_tenant_slug(raw_body)
    tenant = (
        db.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one_or_none()
        if slug else None
    )
    tenant_secrets = list((tenant.settings or {}).get("webhook_secrets", [])) if tenant else []
    secrets = tenant_secrets or settings.webhook_secret_list

    verdict = verify_webhook(
        signature, raw_body, secrets,
        replay_window_seconds=settings.webhook_replay_window_seconds,
    )
    if not verdict.ok:
        # Log the failure class only — never the signature, secrets, or full payload.
        log.warning("webhook rejected", reason=verdict.reason, has_delivery_id=bool(delivery_id))
        WEBHOOKS.labels(source="enrichment", result=verdict.reason).inc()
        response.status_code = (
            status.HTTP_401_UNAUTHORIZED
            if verdict.reason in ("missing_signature", "invalid_signature", "malformed_header")
            else status.HTTP_400_BAD_REQUEST
        )
        return {"accepted": False, "reason": verdict.reason}

    if not delivery_id:
        WEBHOOKS.labels(source="enrichment", result="missing_delivery_id").inc()
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"accepted": False, "reason": "missing X-Delivery-Id header"}

    try:
        payload = WebhookEnrichmentPayload.model_validate_json(raw_body)
    except ValidationError as exc:
        WEBHOOKS.labels(source="enrichment", result="invalid_payload").inc()
        response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        return {"accepted": False, "reason": "invalid payload", "errors": exc.errors()[:5]}

    if tenant is None:
        WEBHOOKS.labels(source="enrichment", result="unknown_tenant").inc()
        response.status_code = status.HTTP_404_NOT_FOUND
        return {"accepted": False, "reason": "unknown tenant"}

    payload_hash = hashlib.sha256(raw_body).hexdigest()
    delivery = WebhookDelivery(
        tenant_id=tenant.id, source="enrichment", delivery_id=delivery_id,
        signature_valid=True, timestamp_valid=True, payload_hash=payload_hash,
        event_meta={"event": payload.event, "entity_type": payload.entity_type},
    )
    db.add(delivery)
    try:
        db.commit()
    except IntegrityError:
        # Duplicate delivery: unique(tenant, source, delivery_id) — return the original
        # outcome and DO NOT create a second job (no duplicate spend).
        db.rollback()
        original = db.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.tenant_id == tenant.id,
                WebhookDelivery.source == "enrichment",
                WebhookDelivery.delivery_id == delivery_id,
            )
        ).scalar_one()
        WEBHOOKS.labels(source="enrichment", result="duplicate").inc()
        log.info("duplicate webhook delivery ignored", delivery=delivery_id)
        return {"accepted": True, "duplicate": True, "job_id": original.job_id}

    data = payload.entity.model_dump(exclude_none=True)
    entity: Contact | Account
    if payload.entity_type == "contact":
        entity, _, _ = match_or_create_contact(db, tenant.id, data)
    else:
        entity, _, _ = match_or_create_account(db, tenant.id, data)
    job = EnrichmentJob(
        tenant_id=tenant.id,
        campaign_id=payload.campaign_id,
        entity_type=payload.entity_type,
        entity_id=entity.id,
        requested_fields=payload.requested_fields,
        status=JobStatus.QUEUED.value,
        metadata_passthrough=payload.metadata,
        idempotency_key=f"webhook:{delivery_id}",
    )
    db.add(job)
    db.flush()  # populate job.id before linking the delivery to it
    delivery.job_id = job.id
    delivery.status = "processed"
    db.commit()

    from relayiq.workers.tasks import run_enrichment_task

    run_enrichment_task.delay(job.id)
    WEBHOOKS.labels(source="enrichment", result="accepted").inc()
    return {"accepted": True, "duplicate": False, "job_id": job.id}
