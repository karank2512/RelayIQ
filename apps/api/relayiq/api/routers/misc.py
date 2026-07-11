"""Health/readiness probes and the audit log."""

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from relayiq.api.deps import Principal, require_operator
from relayiq.db import get_db
from relayiq.models import AuditEvent
from relayiq.schemas.common import Page

router = APIRouter(tags=["system"])


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": "relayiq-api"}


@router.get("/readyz")
def readyz(response: Response, db: Session = Depends(get_db)) -> dict:
    checks: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "failed"
    try:
        from relayiq.services.cache import get_redis

        get_redis().ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "failed"
    ready = all(v == "ok" for v in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": ready, "checks": checks}


@router.get("/v1/audit", response_model=Page[dict])
def audit_log(
    object_type: str | None = None,
    object_id: str | None = None,
    action: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> Page[dict]:
    q = select(AuditEvent).where(AuditEvent.tenant_id == principal.tenant_id)
    if object_type:
        q = q.where(AuditEvent.object_type == object_type)
    if object_id:
        q = q.where(AuditEvent.object_id == object_id)
    if action:
        q = q.where(AuditEvent.action == action)
    total = len(db.execute(q).scalars().all())
    rows = db.execute(
        q.order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return Page(
        items=[
            {
                "id": r.id, "action": r.action, "object_type": r.object_type,
                "object_id": r.object_id, "actor_user_id": r.actor_user_id,
                "actor_type": r.actor_type, "before": r.before, "after": r.after,
                "trace_id": r.trace_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        total=total, limit=limit, offset=offset,
    )
