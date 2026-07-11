"""Review queue endpoints: list, detail (with observations + lineage), actions, reversal."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.api.deps import Principal, require_analyst, require_reviewer
from relayiq.db import get_db
from relayiq.enums import ReviewAction
from relayiq.models import FieldObservation, ReviewDecision, ReviewTask
from relayiq.schemas.common import Page
from relayiq.services import review as review_service
from relayiq.services.lineage import field_lineage

router = APIRouter(prefix="/v1/review", tags=["review"])


class TaskOut(BaseModel):
    id: str
    entity_type: str
    entity_id: str
    field_name: str | None
    reason: str
    status: str
    priority: int
    confidence: float | None
    suggested_value: str | None
    suggested_observation_id: str | None
    job_id: str | None
    created_at: str | None
    resolved_at: str | None

    @classmethod
    def from_model(cls, t: ReviewTask) -> "TaskOut":
        return cls(
            id=t.id, entity_type=t.entity_type, entity_id=t.entity_id, field_name=t.field_name,
            reason=t.reason, status=t.status, priority=t.priority, confidence=t.confidence,
            suggested_value=t.suggested_value, suggested_observation_id=t.suggested_observation_id,
            job_id=t.job_id,
            created_at=t.created_at.isoformat() if t.created_at else None,
            resolved_at=t.resolved_at.isoformat() if t.resolved_at else None,
        )


class ActionIn(BaseModel):
    action: ReviewAction
    selected_observation_id: str | None = None
    corrected_value: str | None = Field(default=None, max_length=1000)
    note: str | None = Field(default=None, max_length=2000)


class DecisionOut(BaseModel):
    id: str
    task_id: str
    action: str
    reviewer_id: str
    corrected_value: str | None
    note: str | None
    previous_state: dict
    reverses_decision_id: str | None
    created_at: str | None

    @classmethod
    def from_model(cls, d: ReviewDecision) -> "DecisionOut":
        return cls(
            id=d.id, task_id=d.task_id, action=d.action, reviewer_id=d.reviewer_id,
            corrected_value=d.corrected_value, note=d.note,
            previous_state=d.previous_state or {}, reverses_decision_id=d.reverses_decision_id,
            created_at=d.created_at.isoformat() if d.created_at else None,
        )


@router.get("/queue", response_model=Page[TaskOut])
def queue(
    status_filter: str | None = Query(default="pending", alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Page[TaskOut]:
    q = select(ReviewTask).where(ReviewTask.tenant_id == principal.tenant_id)
    if status_filter and status_filter != "all":
        q = q.where(ReviewTask.status == status_filter)
    total = len(db.execute(q).scalars().all())
    rows = db.execute(
        q.order_by(ReviewTask.priority, ReviewTask.created_at).limit(limit).offset(offset)
    ).scalars().all()
    return Page(items=[TaskOut.from_model(t) for t in rows], total=total, limit=limit, offset=offset)


@router.get("/metrics")
def metrics(
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    return review_service.queue_metrics(db, principal.tenant_id)


def _get_task(db: Session, principal: Principal, task_id: str) -> ReviewTask:
    task = db.get(ReviewTask, task_id)
    if task is None or task.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "review task not found")
    return task


@router.get("/tasks/{task_id}")
def task_detail(
    task_id: str,
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    """Everything a reviewer needs on one screen: task, all observations with cost/
    freshness/confidence, reconciliation reasoning, and full lineage."""
    task = _get_task(db, principal, task_id)
    decisions = db.execute(
        select(ReviewDecision).where(ReviewDecision.task_id == task.id)
        .order_by(ReviewDecision.created_at)
    ).scalars().all()
    lineage = None
    observations: list[dict] = []
    if task.field_name:
        lineage = field_lineage(db, principal.tenant_id, task.entity_type, task.entity_id,
                                task.field_name)
        observations = lineage["observations"]
    else:
        obs_rows = db.execute(
            select(FieldObservation).where(
                FieldObservation.tenant_id == principal.tenant_id,
                FieldObservation.entity_type == task.entity_type,
                FieldObservation.entity_id == task.entity_id,
            ).order_by(FieldObservation.field_name, FieldObservation.retrieved_at)
        ).scalars().all()
        observations = [
            {
                "id": o.id, "field_name": o.field_name, "provider": o.provider_key,
                "raw_value": o.raw_value, "normalized_value": o.normalized_value,
                "provider_confidence": o.provider_confidence,
                "internal_confidence": o.internal_confidence,
                "cost_credits": float(o.cost_credits or 0),
                "staleness_state": o.staleness_state,
                "is_selected": o.is_selected, "is_rejected": o.is_rejected,
            }
            for o in obs_rows
        ]
    entity = review_service._entity(db, task)
    entity_view = None
    if entity is not None:
        cols = (
            ["full_name", "work_email", "job_title", "seniority", "department", "company_name",
             "company_domain", "country", "record_confidence"]
            if task.entity_type == "contact"
            else ["name", "root_domain", "website", "industry", "employee_count",
                  "employee_range", "hq_city", "hq_country", "record_confidence"]
        )
        entity_view = {c: getattr(entity, c, None) for c in cols}
    return {
        "task": TaskOut.from_model(task).model_dump(),
        "entity": entity_view,
        "observations": observations,
        "decisions": [DecisionOut.from_model(d).model_dump() for d in decisions],
        "lineage": lineage,
    }


@router.post("/tasks/{task_id}/actions", response_model=DecisionOut)
def act(
    task_id: str,
    body: ActionIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_reviewer),
    db: Session = Depends(get_db),
) -> DecisionOut:
    task = _get_task(db, principal, task_id)
    try:
        decision = review_service.apply_action(
            db, task,
            reviewer_id=principal.user_id,
            action=body.action,
            selected_observation_id=body.selected_observation_id,
            corrected_value=body.corrected_value,
            note=body.note,
            idempotency_key=idempotency_key,
        )
    except review_service.ReviewError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    db.commit()
    return DecisionOut.from_model(decision)


@router.post("/tasks/{task_id}/reverse", response_model=DecisionOut)
def reverse(
    task_id: str,
    note: str | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_reviewer),
    db: Session = Depends(get_db),
) -> DecisionOut:
    """Reverse the most recent resolving decision. History is preserved as new records."""
    task = _get_task(db, principal, task_id)
    try:
        decision = review_service.apply_action(
            db, task, reviewer_id=principal.user_id, action=ReviewAction.REVERSE,
            note=note, idempotency_key=idempotency_key,
        )
    except review_service.ReviewError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    db.commit()
    return DecisionOut.from_model(decision)
