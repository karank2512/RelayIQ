"""CRM synchronization endpoints: connections, sync attempts, manual sync, simulator view."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.api.deps import Principal, require_analyst, require_operator
from relayiq.db import get_db
from relayiq.models import Account, Contact, CrmConnection, CrmSimRecord, CrmSyncAttempt
from relayiq.schemas.common import Page
from relayiq.services.crm_sync import sync_entity

router = APIRouter(prefix="/v1/crm", tags=["crm"])


@router.get("/connections")
def list_connections(
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(CrmConnection).where(CrmConnection.tenant_id == principal.tenant_id)
    ).scalars().all()
    return [
        {"id": r.id, "system": r.system, "display_name": r.display_name, "mode": r.mode,
         "is_active": r.is_active, "config": r.config}
        for r in rows
    ]


@router.get("/sync-attempts", response_model=Page[dict])
def list_sync_attempts(
    status_filter: str | None = Query(default=None, alias="status"),
    entity_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Page[dict]:
    q = select(CrmSyncAttempt).where(CrmSyncAttempt.tenant_id == principal.tenant_id)
    if status_filter:
        q = q.where(CrmSyncAttempt.status == status_filter)
    if entity_id:
        q = q.where(CrmSyncAttempt.entity_id == entity_id)
    total = len(db.execute(q).scalars().all())
    rows = db.execute(
        q.order_by(CrmSyncAttempt.created_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return Page(
        items=[
            {
                "id": r.id, "entity_type": r.entity_type, "entity_id": r.entity_id,
                "external_id": r.external_id, "status": r.status, "dry_run": r.dry_run,
                "field_changes": r.field_changes, "gate_summary": r.gate_summary,
                "error": r.error, "synced_at": r.synced_at.isoformat() if r.synced_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        total=total, limit=limit, offset=offset,
    )


class ManualSyncIn(BaseModel):
    entity_type: str = Field(pattern="^(contact|account)$")
    entity_id: str
    fields: list[str] | None = None
    dry_run: bool = False


@router.post("/sync", status_code=status.HTTP_201_CREATED)
def manual_sync(
    body: ManualSyncIn,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    model = Contact if body.entity_type == "contact" else Account
    entity = db.get(model, body.entity_id)
    if entity is None or entity.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")
    attempt = sync_entity(
        db, principal.tenant_id, body.entity_type, entity,
        fields=body.fields, dry_run=body.dry_run,
    )
    db.commit()
    if attempt is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "no active CRM connection or no canonical values to sync")
    return {
        "id": attempt.id, "status": attempt.status, "dry_run": attempt.dry_run,
        "gate_summary": attempt.gate_summary, "field_changes": attempt.field_changes,
    }


@router.get("/simulator/records", response_model=Page[dict])
def simulator_records(
    object_type: str | None = Query(default=None, pattern="^(contact|company)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Page[dict]:
    """What 'the CRM' currently contains (simulator mode) — lets reviewers verify syncs."""
    q = select(CrmSimRecord).where(CrmSimRecord.tenant_id == principal.tenant_id)
    if object_type:
        q = q.where(CrmSimRecord.object_type == object_type)
    total = len(db.execute(q).scalars().all())
    rows = db.execute(
        q.order_by(CrmSimRecord.updated_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return Page(
        items=[
            {"id": r.id, "object_type": r.object_type, "external_id": r.external_id,
             "properties": r.properties, "property_updated_at": r.property_updated_at,
             "updated_at": r.updated_at.isoformat() if r.updated_at else None}
            for r in rows
        ],
        total=total, limit=limit, offset=offset,
    )
