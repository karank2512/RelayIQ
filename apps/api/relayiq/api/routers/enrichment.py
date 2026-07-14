"""Enrichment sidecar endpoints — the Clay-compatible contract.

NOTE ON CLAY COMPATIBILITY: Clay calls external services through its generic
"HTTP API" enrichment column (POST with JSON body + custom headers). These endpoints
implement that sidecar contract (idempotency key, requested fields, callback URL,
metadata passthrough). A live Clay integration has NOT been tested in this build —
see docs/architecture/clay-integration.md for the mapping and unverified assumptions.
"""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.api.deps import Principal, require_analyst, require_operator
from relayiq.db import get_db
from relayiq.engines import decision as decision_engine
from relayiq.engines.orchestrator import run_enrichment_job
from relayiq.enums import EntityType, JobStatus
from relayiq.models import Campaign, EnrichmentJob
from relayiq.providers.registry import get_registry
from relayiq.schemas.common import Page
from relayiq.schemas.enrichment import (
    BatchOut,
    BatchRequestIn,
    DecideOut,
    EnrichmentRequestIn,
    JobOut,
)
from relayiq.services import budget as budget_service
from relayiq.services import idempotency
from relayiq.services.entities import match_or_create_account, match_or_create_contact
from relayiq.services.ssrf import validate_callback_url

router = APIRouter(prefix="/v1/enrichment", tags=["enrichment"])


def _resolve_entity(db: Session, tenant_id: str, body: EnrichmentRequestIn):
    data = body.entity.model_dump(exclude_none=True)
    if body.entity_type == EntityType.CONTACT.value:
        return match_or_create_contact(db, tenant_id, data)
    return match_or_create_account(db, tenant_id, data)


def _check_callback(url: str | None) -> None:
    if url is None:
        return
    from relayiq.config import get_settings

    check = validate_callback_url(url, allow_private=not get_settings().is_production)
    if not check.ok:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"callback_url rejected: {check.reason}"
        )


def _campaign(db: Session, principal: Principal, campaign_id: str | None) -> Campaign | None:
    if campaign_id is None:
        return None
    campaign = db.get(Campaign, campaign_id)
    if campaign is None or campaign.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not found")
    return campaign


@router.post("/decide", response_model=DecideOut)
def decide(
    body: EnrichmentRequestIn,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> DecideOut:
    """Dry-run decision: would RelayIQ enrich this row, and what would it cost?
    Spends nothing, creates no job."""
    _check_callback(body.callback_url)
    campaign = _campaign(db, principal, body.campaign_id)
    entity, _, _ = _resolve_entity(db, principal.tenant_id, body)
    registry = get_registry(db, refresh=True)
    est = sum(
        min(
            (a.field_cost(body.entity_type, f) for a in registry.all().values()
             if a.supports(body.entity_type, f)),
            default=0.0,
        )
        for f in body.requested_fields
    )
    budget = budget_service.get_active_budget(db, principal.tenant_id, body.campaign_id)
    bstate = budget_service.check(db, budget, est)
    identifiers = body.entity.model_dump(exclude_none=True)
    out = decision_engine.decide(
        db,
        decision_engine.DecisionInput(
            tenant_id=principal.tenant_id, entity_type=body.entity_type, entity_id=entity.id,
            requested_fields=body.requested_fields, identifiers=identifiers,
            campaign=campaign, budget_state=bstate,
            providers_available=any(registry.available(k) for k in registry.all()),
            estimated_min_cost=est,
        ),
    )
    db.commit()
    return DecideOut(
        decision=out.decision.value, reasons=out.reasons,
        fields_to_enrich=out.fields_to_enrich, fields_from_cache=out.fields_from_cache,
        estimated_cost_credits=round(est, 4), budget_warning=bstate.warning,
    )


@router.post("/execute", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def execute(
    body: EnrichmentRequestIn,
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Single-row enrichment. Idempotent via Idempotency-Key header or body key:
    replays return the recorded response and spend nothing."""
    _check_callback(body.callback_url)
    campaign = _campaign(db, principal, body.campaign_id)
    idem_key = body.idempotency_key or idempotency_key_header
    payload_hash = idempotency.request_hash(body.model_dump(mode="json", exclude={"idempotency_key"}))
    claim = None
    if idem_key:
        claim = idempotency.claim(db, principal.tenant_id, "enrichment", idem_key, payload_hash)
        if claim.outcome == idempotency.ClaimOutcome.COMPLETED:
            return JobOut(**(claim.response_snapshot or {}))
        if claim.outcome == idempotency.ClaimOutcome.IN_PROGRESS:
            raise HTTPException(status.HTTP_409_CONFLICT, "identical request is already in progress")
        if claim.outcome == idempotency.ClaimOutcome.MISMATCH:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "idempotency key was already used with a different payload",
            )

    entity, _, _ = _resolve_entity(db, principal.tenant_id, body)
    job = EnrichmentJob(
        tenant_id=principal.tenant_id,
        campaign_id=campaign.id if campaign else None,
        budget_id=body.budget_id,
        entity_type=body.entity_type,
        entity_id=entity.id,
        requested_fields=body.requested_fields,
        status=JobStatus.RECEIVED.value if body.mode == "sync" else JobStatus.QUEUED.value,
        dry_run=body.dry_run,
        idempotency_key=idem_key,
        callback_url=body.callback_url,
        metadata_passthrough=body.metadata,
    )
    db.add(job)
    db.commit()

    try:
        if body.mode == "sync":
            run_enrichment_job(db, job.id)
        else:
            from relayiq.workers.tasks import run_enrichment_task

            run_enrichment_task.delay(job.id)
    except Exception:
        if claim and claim.record:
            idempotency.fail(db, claim.record)
        raise

    db.refresh(job)
    out = JobOut.from_model(job)
    if claim and claim.record:
        idempotency.complete(db, claim.record, out.model_dump(mode="json"))
    return out


@router.post("/batch", response_model=BatchOut, status_code=status.HTTP_202_ACCEPTED)
def batch(
    body: BatchRequestIn,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> BatchOut:
    """Batch enrichment: one async job per row, grouped under a batch id."""
    _check_callback(body.callback_url)
    campaign = _campaign(db, principal, body.campaign_id)
    batch_id = str(uuid.uuid4())
    if body.idempotency_key:
        claim = idempotency.claim(
            db, principal.tenant_id, "enrichment_batch", body.idempotency_key,
            idempotency.request_hash(body.model_dump(mode="json", exclude={"idempotency_key"})),
        )
        if claim.outcome == idempotency.ClaimOutcome.COMPLETED:
            return BatchOut(**(claim.response_snapshot or {}))
        if claim.outcome == idempotency.ClaimOutcome.IN_PROGRESS:
            raise HTTPException(status.HTTP_409_CONFLICT, "batch already in progress")
        if claim.outcome == idempotency.ClaimOutcome.MISMATCH:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "idempotency key reused")
    else:
        claim = None

    job_ids: list[str] = []
    for row in body.rows:
        req = EnrichmentRequestIn(
            entity_type=body.entity_type, entity=row, requested_fields=body.requested_fields,
            campaign_id=body.campaign_id, dry_run=body.dry_run, mode="async",
        )
        entity, _, _ = _resolve_entity(db, principal.tenant_id, req)
        job = EnrichmentJob(
            tenant_id=principal.tenant_id,
            campaign_id=campaign.id if campaign else None,
            entity_type=body.entity_type,
            entity_id=entity.id,
            requested_fields=body.requested_fields,
            status=JobStatus.QUEUED.value,
            dry_run=body.dry_run,
            callback_url=body.callback_url,
            batch_id=batch_id,
        )
        db.add(job)
        db.flush()
        job_ids.append(job.id)
    db.commit()

    from relayiq.workers.tasks import run_enrichment_task

    for jid in job_ids:
        run_enrichment_task.delay(jid)

    out = BatchOut(batch_id=batch_id, job_ids=job_ids, queued=len(job_ids))
    if claim and claim.record:
        idempotency.complete(db, claim.record, out.model_dump(mode="json"))
    return out


@router.get("/jobs", response_model=Page[JobOut])
def list_jobs(
    status_filter: str | None = Query(default=None, alias="status"),
    batch_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Page[JobOut]:
    q = select(EnrichmentJob).where(EnrichmentJob.tenant_id == principal.tenant_id)
    if status_filter:
        q = q.where(EnrichmentJob.status == status_filter)
    if batch_id:
        q = q.where(EnrichmentJob.batch_id == batch_id)
    total = len(db.execute(q).scalars().all())
    rows = db.execute(
        q.order_by(EnrichmentJob.created_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return Page(items=[JobOut.from_model(j) for j in rows], total=total, limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: str,
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> JobOut:
    job = db.get(EnrichmentJob, job_id)
    if job is None or job.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return JobOut.from_model(job)
