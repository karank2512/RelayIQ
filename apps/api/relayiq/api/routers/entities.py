"""Accounts, contacts, and lineage endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from relayiq.api.deps import Principal, require_analyst
from relayiq.db import get_db
from relayiq.models import Account, CanonicalFieldValue, Contact
from relayiq.schemas.common import Page
from relayiq.services.lineage import entity_lineage, field_lineage

router = APIRouter(prefix="/v1", tags=["entities"])

ACCOUNT_COLS = ["id", "external_crm_id", "name", "normalized_name", "website", "root_domain",
                "linkedin_url", "industry", "sub_industry", "employee_count", "employee_range",
                "annual_revenue_usd", "hq_city", "hq_state", "hq_country", "company_type",
                "founded_year", "technology_signals", "record_status", "record_confidence"]
CONTACT_COLS = ["id", "external_crm_id", "first_name", "last_name", "full_name", "work_email",
                "email_status", "job_title", "normalized_job_title", "seniority", "department",
                "account_id", "company_name", "company_domain", "country", "linkedin_url",
                "record_status", "record_confidence"]


def _dump(entity, cols: list[str]) -> dict:
    doc = {c: getattr(entity, c, None) for c in cols}
    doc["last_verified_at"] = entity.last_verified_at.isoformat() if entity.last_verified_at else None
    doc["created_at"] = entity.created_at.isoformat() if entity.created_at else None
    doc["updated_at"] = entity.updated_at.isoformat() if entity.updated_at else None
    return doc


@router.get("/accounts", response_model=Page[dict])
def list_accounts(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Page[dict]:
    query = select(Account).where(Account.tenant_id == principal.tenant_id)
    if q:
        like = f"%{q.lower()}%"
        query = query.where(or_(Account.normalized_name.ilike(like), Account.root_domain.ilike(like)))
    total = len(db.execute(query).scalars().all())
    rows = db.execute(query.order_by(Account.created_at.desc()).limit(limit).offset(offset)).scalars()
    return Page(items=[_dump(a, ACCOUNT_COLS) for a in rows], total=total, limit=limit, offset=offset)


@router.get("/contacts", response_model=Page[dict])
def list_contacts(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Page[dict]:
    query = select(Contact).where(Contact.tenant_id == principal.tenant_id)
    if q:
        like = f"%{q.lower()}%"
        query = query.where(or_(
            Contact.full_name.ilike(like), Contact.work_email.ilike(like),
            Contact.company_domain.ilike(like),
        ))
    total = len(db.execute(query).scalars().all())
    rows = db.execute(query.order_by(Contact.created_at.desc()).limit(limit).offset(offset)).scalars()
    return Page(items=[_dump(c, CONTACT_COLS) for c in rows], total=total, limit=limit, offset=offset)


def _entity(db: Session, principal: Principal, entity_type: str, entity_id: str):
    model = Contact if entity_type == "contact" else Account
    entity = db.get(model, entity_id)
    if entity is None or entity.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{entity_type} not found")
    return entity


@router.get("/entities/{entity_type}/{entity_id}")
def entity_detail(
    entity_type: str,
    entity_id: str,
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    if entity_type not in ("contact", "account"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "entity_type must be contact|account")
    entity = _entity(db, principal, entity_type, entity_id)
    canon = db.execute(
        select(CanonicalFieldValue).where(
            CanonicalFieldValue.tenant_id == principal.tenant_id,
            CanonicalFieldValue.entity_type == entity_type,
            CanonicalFieldValue.entity_id == entity_id,
        ).order_by(CanonicalFieldValue.field_name)
    ).scalars().all()
    return {
        "entity": _dump(entity, CONTACT_COLS if entity_type == "contact" else ACCOUNT_COLS),
        "canonical_fields": [
            {
                "field_name": r.field_name, "value": r.value, "normalized_value": r.normalized_value,
                "confidence": r.confidence, "staleness_state": r.staleness_state,
                "source_kind": r.source_kind, "locked": r.locked,
                "selected_observation_id": r.selected_observation_id,
                "last_verified_at": r.last_verified_at.isoformat() if r.last_verified_at else None,
            }
            for r in canon
        ],
    }


@router.get("/entities/{entity_type}/{entity_id}/lineage")
def get_entity_lineage(
    entity_type: str,
    entity_id: str,
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    _entity(db, principal, entity_type, entity_id)
    return entity_lineage(db, principal.tenant_id, entity_type, entity_id)


@router.get("/entities/{entity_type}/{entity_id}/lineage/{field_name}")
def get_field_lineage(
    entity_type: str,
    entity_id: str,
    field_name: str,
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    _entity(db, principal, entity_type, entity_id)
    return field_lineage(db, principal.tenant_id, entity_type, entity_id, field_name)
