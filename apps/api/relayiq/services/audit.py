"""Append-only audit events for every state-changing action."""

from sqlalchemy.orm import Session

from relayiq.models import AuditEvent


def record(
    session: Session,
    tenant_id: str,
    *,
    action: str,
    object_type: str,
    object_id: str | None,
    actor_user_id: str | None = None,
    actor_type: str = "system",
    before: dict | None = None,
    after: dict | None = None,
    trace_id: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        action=action,
        object_type=object_type,
        object_id=object_id,
        before=before or {},
        after=after or {},
        trace_id=trace_id,
    )
    session.add(event)
    return event
