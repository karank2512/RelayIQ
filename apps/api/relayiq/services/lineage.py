"""Source lineage: reconstruct the full decision chain for an entity or a single field —
input → pre-decision → routing → provider call → observation → reconciliation →
confidence → review → CRM sync. Read-only queries over the decision tables."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.models import (
    CanonicalFieldValue,
    ConfidenceEvaluation,
    CrmSyncAttempt,
    EnrichmentJob,
    FieldObservation,
    ProviderRequest,
    ReconciliationDecision,
    ReviewDecision,
    ReviewTask,
    RoutingDecision,
    WorkflowStep,
)


def _iso(ts) -> str | None:
    return ts.isoformat() if ts else None


def field_lineage(session: Session, tenant_id: str, entity_type: str, entity_id: str,
                  field_name: str) -> dict:
    canonical = session.execute(
        select(CanonicalFieldValue).where(
            CanonicalFieldValue.tenant_id == tenant_id,
            CanonicalFieldValue.entity_type == entity_type,
            CanonicalFieldValue.entity_id == entity_id,
            CanonicalFieldValue.field_name == field_name,
        )
    ).scalar_one_or_none()

    routing = session.execute(
        select(RoutingDecision).where(
            RoutingDecision.tenant_id == tenant_id,
            RoutingDecision.entity_type == entity_type,
            RoutingDecision.entity_id == entity_id,
            RoutingDecision.field_name == field_name,
        ).order_by(RoutingDecision.created_at)
    ).scalars().all()

    observations = session.execute(
        select(FieldObservation).where(
            FieldObservation.tenant_id == tenant_id,
            FieldObservation.entity_type == entity_type,
            FieldObservation.entity_id == entity_id,
            FieldObservation.field_name == field_name,
        ).order_by(FieldObservation.retrieved_at)
    ).scalars().all()

    provider_request_ids = {o.provider_request_id for o in observations if o.provider_request_id}
    provider_requests = session.execute(
        select(ProviderRequest).where(ProviderRequest.id.in_(provider_request_ids))
    ).scalars().all() if provider_request_ids else []

    reconciliations = session.execute(
        select(ReconciliationDecision).where(
            ReconciliationDecision.tenant_id == tenant_id,
            ReconciliationDecision.entity_type == entity_type,
            ReconciliationDecision.entity_id == entity_id,
            ReconciliationDecision.field_name == field_name,
        ).order_by(ReconciliationDecision.created_at)
    ).scalars().all()

    confidences = session.execute(
        select(ConfidenceEvaluation).where(
            ConfidenceEvaluation.tenant_id == tenant_id,
            ConfidenceEvaluation.entity_type == entity_type,
            ConfidenceEvaluation.entity_id == entity_id,
            ConfidenceEvaluation.field_name == field_name,
        ).order_by(ConfidenceEvaluation.created_at)
    ).scalars().all()

    tasks = session.execute(
        select(ReviewTask).where(
            ReviewTask.tenant_id == tenant_id,
            ReviewTask.entity_type == entity_type,
            ReviewTask.entity_id == entity_id,
            ReviewTask.field_name == field_name,
        )
    ).scalars().all()
    task_ids = [t.id for t in tasks]
    review_decisions = session.execute(
        select(ReviewDecision).where(ReviewDecision.task_id.in_(task_ids))
        .order_by(ReviewDecision.created_at)
    ).scalars().all() if task_ids else []

    syncs = session.execute(
        select(CrmSyncAttempt).where(
            CrmSyncAttempt.tenant_id == tenant_id,
            CrmSyncAttempt.entity_type == entity_type,
            CrmSyncAttempt.entity_id == entity_id,
        ).order_by(CrmSyncAttempt.created_at)
    ).scalars().all()
    field_syncs = [s for s in syncs if field_name in (s.field_changes or {})]

    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "field_name": field_name,
        "canonical": {
            "value": canonical.value,
            "normalized_value": canonical.normalized_value,
            "confidence": canonical.confidence,
            "staleness_state": canonical.staleness_state,
            "source_kind": canonical.source_kind,
            "selected_observation_id": canonical.selected_observation_id,
            "locked": canonical.locked,
            "last_verified_at": _iso(canonical.last_verified_at),
        } if canonical else None,
        "routing_decisions": [
            {
                "id": r.id, "job_id": r.job_id, "strategy": r.strategy,
                "selected_provider": r.selected_provider,
                "candidates": r.candidates, "rejected_providers": r.rejected_providers,
                "factors": r.factors, "expected_cost": float(r.expected_cost_credits or 0),
                "actual_cost": float(r.actual_cost_credits) if r.actual_cost_credits is not None else None,
                "fallback_used": r.fallback_used, "at": _iso(r.created_at),
            }
            for r in routing
        ],
        "provider_requests": [
            {
                "id": p.id, "provider": p.provider_key, "outcome": p.outcome,
                "retry_count": p.retry_count, "latency_ms": p.latency_ms,
                "cost_credits": float(p.cost_credits or 0), "error": p.error_type,
                "trace_id": p.trace_id, "at": _iso(p.created_at),
            }
            for p in provider_requests
        ],
        "observations": [
            {
                "id": o.id, "provider": o.provider_key, "raw_value": o.raw_value,
                "normalized_value": o.normalized_value,
                "provider_confidence": o.provider_confidence,
                "internal_confidence": o.internal_confidence,
                "cost_credits": float(o.cost_credits or 0),
                "latency_ms": o.provider_latency_ms,
                "staleness_state": o.staleness_state,
                "validation": o.validation_results,
                "is_selected": o.is_selected, "is_rejected": o.is_rejected,
                "rejection_reason": o.rejection_reason, "review_status": o.review_status,
                "source_timestamp": _iso(o.source_timestamp), "retrieved_at": _iso(o.retrieved_at),
                "trace_id": o.trace_id, "job_id": o.workflow_id,
            }
            for o in observations
        ],
        "reconciliations": [
            {
                "id": r.id, "outcome": r.outcome, "chosen_value": r.chosen_value,
                "chosen_observation_id": r.chosen_observation_id,
                "reasoning": r.reasoning, "factors": r.factors,
                "conflict_severity": r.conflict_severity, "at": _iso(r.created_at),
            }
            for r in reconciliations
        ],
        "confidence_evaluations": [
            {
                "id": c.id, "level": c.level, "score": c.score,
                "components": c.components, "formula_version": c.formula_version,
                "at": _iso(c.created_at),
            }
            for c in confidences
        ],
        "review": {
            "tasks": [
                {
                    "id": t.id, "status": t.status, "reason": t.reason,
                    "confidence": t.confidence, "suggested_value": t.suggested_value,
                    "at": _iso(t.created_at),
                }
                for t in tasks
            ],
            "decisions": [
                {
                    "id": d.id, "task_id": d.task_id, "action": d.action,
                    "reviewer_id": d.reviewer_id, "corrected_value": d.corrected_value,
                    "note": d.note, "previous_state": d.previous_state,
                    "reverses_decision_id": d.reverses_decision_id, "at": _iso(d.created_at),
                }
                for d in review_decisions
            ],
        },
        "crm_syncs": [
            {
                "id": s.id, "status": s.status, "dry_run": s.dry_run,
                "change": (s.field_changes or {}).get(field_name),
                "external_id": s.external_id, "at": _iso(s.created_at),
            }
            for s in field_syncs
        ],
    }


def entity_lineage(session: Session, tenant_id: str, entity_type: str, entity_id: str) -> dict:
    jobs = session.execute(
        select(EnrichmentJob).where(
            EnrichmentJob.tenant_id == tenant_id,
            EnrichmentJob.entity_type == entity_type,
            EnrichmentJob.entity_id == entity_id,
        ).order_by(EnrichmentJob.created_at)
    ).scalars().all()
    job_ids = [j.id for j in jobs]
    steps = session.execute(
        select(WorkflowStep).where(WorkflowStep.job_id.in_(job_ids))
        .order_by(WorkflowStep.job_id, WorkflowStep.sequence)
    ).scalars().all() if job_ids else []

    fields = session.execute(
        select(CanonicalFieldValue.field_name).where(
            CanonicalFieldValue.tenant_id == tenant_id,
            CanonicalFieldValue.entity_type == entity_type,
            CanonicalFieldValue.entity_id == entity_id,
        )
    ).scalars().all()

    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "jobs": [
            {
                "id": j.id, "status": j.status, "pre_decision": j.pre_decision,
                "decision_reasons": j.decision_reasons, "requested_fields": j.requested_fields,
                "estimated_cost": float(j.estimated_cost_credits or 0),
                "actual_cost": float(j.actual_cost_credits or 0),
                "result_summary": j.result_summary, "trace_id": j.trace_id,
                "at": _iso(j.created_at),
                "steps": [
                    {"name": s.step_name, "status": s.status, "detail": s.detail,
                     "started_at": _iso(s.started_at), "finished_at": _iso(s.finished_at)}
                    for s in steps if s.job_id == j.id
                ],
            }
            for j in jobs
        ],
        "fields": sorted(fields),
    }
