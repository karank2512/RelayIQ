"""Manual review workflow: create tasks, apply reviewer actions, reverse approvals.

Every action snapshots prior state into review_decisions.previous_state (lossless reversal)
and lands in the audit log. Actions are idempotent via optional idempotency keys.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.enums import ReviewAction, ReviewTaskStatus, StalenessState
from relayiq.models import CanonicalFieldValue, FieldObservation, ReviewDecision, ReviewTask
from relayiq.observability.metrics import QUEUE_DEPTH, REVIEW_ACTIONS
from relayiq.services import audit
from relayiq.services.cache import FieldCache
from relayiq.services.entities import apply_canonical_to_entity, upsert_canonical_value
from relayiq.services.staleness import review_priority_boost


class ReviewError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def create_task(
    session: Session,
    tenant_id: str,
    *,
    entity_type: str,
    entity_id: str,
    field_name: str | None,
    reason: str,
    job_id: str | None = None,
    reconciliation_decision_id: str | None = None,
    confidence: float | None = None,
    suggested_value: str | None = None,
    suggested_observation_id: str | None = None,
    staleness_state: StalenessState = StalenessState.UNKNOWN,
) -> ReviewTask:
    # One open task per (entity, field): re-raising the same conflict updates the task.
    existing = session.execute(
        select(ReviewTask).where(
            ReviewTask.tenant_id == tenant_id,
            ReviewTask.entity_type == entity_type,
            ReviewTask.entity_id == entity_id,
            ReviewTask.field_name == field_name,
            ReviewTask.status.in_([ReviewTaskStatus.PENDING.value, ReviewTaskStatus.DEFERRED.value]),
        )
    ).scalars().first()
    if existing:
        existing.reason = reason
        existing.confidence = confidence
        existing.suggested_value = suggested_value
        existing.suggested_observation_id = suggested_observation_id
        existing.job_id = job_id or existing.job_id
        session.flush()
        return existing
    task = ReviewTask(
        tenant_id=tenant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        field_name=field_name,
        reason=reason,
        job_id=job_id,
        reconciliation_decision_id=reconciliation_decision_id,
        confidence=confidence,
        suggested_value=suggested_value,
        suggested_observation_id=suggested_observation_id,
        priority=50 + review_priority_boost(staleness_state) - int((1 - (confidence or 0.5)) * 20),
    )
    session.add(task)
    session.flush()
    return task


def _snapshot(session: Session, task: ReviewTask) -> dict:
    canonical = session.execute(
        select(CanonicalFieldValue).where(
            CanonicalFieldValue.tenant_id == task.tenant_id,
            CanonicalFieldValue.entity_type == task.entity_type,
            CanonicalFieldValue.entity_id == task.entity_id,
            CanonicalFieldValue.field_name == (task.field_name or ""),
        )
    ).scalar_one_or_none()
    return {
        "task_status": task.status,
        "canonical_value": canonical.value if canonical else None,
        "canonical_confidence": canonical.confidence if canonical else None,
        "canonical_observation_id": canonical.selected_observation_id if canonical else None,
    }


def _entity(session: Session, task: ReviewTask):
    from relayiq.models import Account, Contact

    model = Contact if task.entity_type == "contact" else Account
    return session.get(model, task.entity_id)


def apply_action(
    session: Session,
    task: ReviewTask,
    *,
    reviewer_id: str,
    action: ReviewAction,
    selected_observation_id: str | None = None,
    corrected_value: str | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
    cache: FieldCache | None = None,
) -> ReviewDecision:
    if idempotency_key:
        prior = session.execute(
            select(ReviewDecision).where(
                ReviewDecision.tenant_id == task.tenant_id,
                ReviewDecision.idempotency_key == idempotency_key,
            )
        ).scalars().first()
        if prior:
            return prior  # duplicate submission — replay recorded decision

    terminal = (ReviewTaskStatus.ACCEPTED.value, ReviewTaskStatus.OVERRIDDEN.value,
                ReviewTaskStatus.REJECTED.value)
    if action != ReviewAction.REVERSE and task.status in terminal:
        raise ReviewError(f"task already resolved ({task.status}); reverse it first", 409)

    decision = ReviewDecision(
        tenant_id=task.tenant_id,
        task_id=task.id,
        reviewer_id=reviewer_id,
        action=action.value,
        selected_observation_id=selected_observation_id,
        corrected_value=corrected_value,
        note=note,
        previous_state=_snapshot(session, task),
        idempotency_key=idempotency_key,
    )
    session.add(decision)

    now = datetime.now(UTC)
    if task.first_opened_at is None:
        task.first_opened_at = now

    if action == ReviewAction.ACCEPT_SUGGESTED:
        _apply_value(session, task, task.suggested_observation_id, task.suggested_value, decision, cache)
        task.status = ReviewTaskStatus.ACCEPTED.value
        task.resolved_at = now
    elif action == ReviewAction.SELECT_OBSERVATION:
        if not selected_observation_id:
            raise ReviewError("selected_observation_id is required for select_observation")
        obs = session.get(FieldObservation, selected_observation_id)
        if obs is None or obs.tenant_id != task.tenant_id:
            raise ReviewError("observation not found", 404)
        _apply_value(session, task, obs.id, obs.normalized_value or obs.raw_value, decision, cache)
        task.status = ReviewTaskStatus.OVERRIDDEN.value
        task.resolved_at = now
    elif action == ReviewAction.CORRECT_VALUE:
        if corrected_value in (None, ""):
            raise ReviewError("corrected_value is required for correct_value")
        _apply_value(session, task, None, corrected_value, decision, cache, source_kind="manual")
        task.status = ReviewTaskStatus.OVERRIDDEN.value
        task.resolved_at = now
    elif action == ReviewAction.REJECT:
        _reject_observations(session, task, "reviewer rejected the record")
        task.status = ReviewTaskStatus.REJECTED.value
        task.resolved_at = now
    elif action == ReviewAction.DEFER:
        task.status = ReviewTaskStatus.DEFERRED.value
    elif action == ReviewAction.ADD_NOTE:
        if not note:
            raise ReviewError("note text is required")
    elif action == ReviewAction.REVERSE:
        _reverse(session, task, decision, cache)
    else:  # pragma: no cover
        raise ReviewError(f"unsupported action {action}")

    REVIEW_ACTIONS.labels(action=action.value).inc()
    audit.record(
        session, task.tenant_id,
        action=f"review.{action.value}", object_type="review_task", object_id=task.id,
        actor_user_id=reviewer_id, actor_type="user",
        before=decision.previous_state,
        after={"task_status": task.status, "value": corrected_value or task.suggested_value},
    )
    pending = session.execute(
        select(ReviewTask).where(ReviewTask.status == ReviewTaskStatus.PENDING.value)
    ).scalars().all()
    QUEUE_DEPTH.set(len(pending))
    session.flush()
    return decision


def _apply_value(
    session: Session,
    task: ReviewTask,
    observation_id: str | None,
    value: str | None,
    decision: ReviewDecision,
    cache: FieldCache | None,
    source_kind: str = "review",
) -> None:
    if task.field_name is None:
        return  # record-level approval: no single field to write
    from relayiq.canonical.normalize import normalize_value

    normalized = normalize_value(task.field_name, str(value)) if value is not None else None
    row = upsert_canonical_value(
        session, task.tenant_id, task.entity_type, task.entity_id, task.field_name,
        value=value, normalized_value=normalized, confidence=0.95 if source_kind == "review" else 0.9,
        observation_id=observation_id, reconciliation_decision_id=task.reconciliation_decision_id,
        staleness_state=StalenessState.FRESH.value, source_kind=source_kind,
    )
    # Selection flags: chosen observation selected, others deselected (never deleted).
    all_obs = session.execute(
        select(FieldObservation).where(
            FieldObservation.tenant_id == task.tenant_id,
            FieldObservation.entity_type == task.entity_type,
            FieldObservation.entity_id == task.entity_id,
            FieldObservation.field_name == task.field_name,
        )
    ).scalars().all()
    for o in all_obs:
        o.is_selected = o.id == observation_id
        o.review_status = "reviewed"
    entity = _entity(session, task)
    if entity is not None:
        apply_canonical_to_entity(session, task.entity_type, entity, task.field_name, str(value))
        if cache is not None:
            from relayiq.services.entities import entity_lookup_key

            key = entity_lookup_key(task.entity_type, entity)
            cache.set_field(
                task.tenant_id, task.entity_type, key, task.field_name,
                value=value, normalized_value=normalized, provider_key=None,
                confidence=row.confidence, observation_id=observation_id,
            )


def _reject_observations(session: Session, task: ReviewTask, reason: str) -> None:
    q = select(FieldObservation).where(
        FieldObservation.tenant_id == task.tenant_id,
        FieldObservation.entity_type == task.entity_type,
        FieldObservation.entity_id == task.entity_id,
    )
    if task.field_name:
        q = q.where(FieldObservation.field_name == task.field_name)
    for o in session.execute(q).scalars():
        o.is_rejected = True
        o.is_selected = False
        o.rejection_reason = reason
        o.review_status = "reviewed"


def _reverse(session: Session, task: ReviewTask, decision: ReviewDecision, cache: FieldCache | None) -> None:
    """Restore the state captured by the most recent resolving decision. History is preserved:
    the reversal is itself a new appended decision."""
    last = session.execute(
        select(ReviewDecision)
        .where(
            ReviewDecision.task_id == task.id,
            ReviewDecision.action.in_([
                ReviewAction.ACCEPT_SUGGESTED.value, ReviewAction.SELECT_OBSERVATION.value,
                ReviewAction.CORRECT_VALUE.value, ReviewAction.REJECT.value,
            ]),
        )
        .order_by(ReviewDecision.created_at.desc())
    ).scalars().first()
    if last is None:
        raise ReviewError("nothing to reverse: no resolving decision found", 409)
    decision.reverses_decision_id = last.id
    prev = last.previous_state or {}
    if task.field_name:
        upsert_canonical_value(
            session, task.tenant_id, task.entity_type, task.entity_id, task.field_name,
            value=prev.get("canonical_value"),
            normalized_value=prev.get("canonical_value"),
            confidence=prev.get("canonical_confidence"),
            observation_id=prev.get("canonical_observation_id"),
            reconciliation_decision_id=task.reconciliation_decision_id,
            staleness_state=StalenessState.UNKNOWN.value,
            source_kind="reversal",
        )
        entity = _entity(session, task)
        if entity is not None:
            apply_canonical_to_entity(
                session, task.entity_type, entity, task.field_name, prev.get("canonical_value")
            )
            if cache is not None:
                from relayiq.services.entities import entity_lookup_key

                cache.invalidate_field(
                    task.tenant_id, task.entity_type, entity_lookup_key(task.entity_type, entity),
                    task.field_name,
                )
    task.status = ReviewTaskStatus.REVERSED.value
    task.resolved_at = None


def queue_metrics(session: Session, tenant_id: str) -> dict:
    tasks = session.execute(
        select(ReviewTask).where(ReviewTask.tenant_id == tenant_id)
    ).scalars().all()
    decisions = session.execute(
        select(ReviewDecision).where(ReviewDecision.tenant_id == tenant_id)
    ).scalars().all()
    resolved = [t for t in tasks if t.status in ("accepted", "overridden", "rejected")]
    accepted = [t for t in resolved if t.status == "accepted"]
    overridden = [t for t in resolved if t.status == "overridden"]
    reversals = [d for d in decisions if d.action == "reverse"]
    durations = [
        (t.resolved_at - t.first_opened_at).total_seconds()
        for t in resolved
        if t.resolved_at and t.first_opened_at
    ]
    conf_by_status: dict[str, list[float]] = {}
    for t in resolved:
        if t.confidence is not None:
            conf_by_status.setdefault(t.status, []).append(t.confidence)
    return {
        "pending": len([t for t in tasks if t.status == "pending"]),
        "deferred": len([t for t in tasks if t.status == "deferred"]),
        "resolved": len(resolved),
        "acceptance_rate": len(accepted) / len(resolved) if resolved else None,
        "override_rate": len(overridden) / len(resolved) if resolved else None,
        "reversal_rate": len(reversals) / len(resolved) if resolved else None,
        "avg_review_seconds": sum(durations) / len(durations) if durations else None,
        "avg_confidence_by_decision": {
            k: round(sum(v) / len(v), 4) for k, v in conf_by_status.items()
        },
    }
