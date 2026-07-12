"""Admin/operator configuration: providers, routing policies, staleness policies,
campaigns, budgets. Mutations require admin (providers/budgets) or operator (campaigns)."""

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.api.deps import Principal, require_admin, require_analyst, require_operator
from relayiq.db import get_db
from relayiq.enums import BudgetKind, BudgetPeriod
from relayiq.models import (
    Budget,
    Campaign,
    ProviderConfig,
    RoutingPolicy,
    StalenessPolicy,
    Suppression,
)
from relayiq.providers.registry import get_registry
from relayiq.services import audit
from relayiq.services.provider_exec import provider_stats

router = APIRouter(prefix="/v1/admin", tags=["admin"])


# ── Providers ───────────────────────────────────────────────────────────────

@router.get("/providers")
def list_providers(
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    registry = get_registry(db, refresh=True)
    rows = db.execute(select(ProviderConfig)).scalars().all()
    out = []
    for cfg in rows:
        adapter = registry.get(cfg.key)
        breaker = registry.breaker(cfg.key)
        out.append({
            "id": cfg.id, "key": cfg.key, "display_name": cfg.display_name,
            "adapter": cfg.adapter, "version": cfg.version, "enabled": cfg.enabled,
            "timeout_ms": cfg.timeout_ms, "max_retries": cfg.max_retries,
            "reliability_prior": cfg.reliability_prior,
            "rate_limit_per_minute": cfg.rate_limit_per_minute,
            "simulation": adapter.simulation_mode if adapter else None,
            "circuit_state": breaker.state,
            "capabilities": (
                {et: sorted(fs) for et, fs in adapter.capabilities().items()} if adapter else {}
            ),
            "stats_24h": provider_stats(db, cfg.key, hours=24),
        })
    return out


class ProviderUpdateIn(BaseModel):
    enabled: bool | None = None
    timeout_ms: int | None = Field(default=None, ge=100, le=60000)
    max_retries: int | None = Field(default=None, ge=0, le=5)
    reliability_prior: float | None = Field(default=None, ge=0, le=1)
    rate_limit_per_minute: int | None = Field(default=None, ge=1)
    config: dict | None = None


@router.patch("/providers/{provider_key}")
def update_provider(
    provider_key: str,
    body: ProviderUpdateIn,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    cfg = db.execute(
        select(ProviderConfig).where(ProviderConfig.key == provider_key)
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "provider not found")
    before = {"enabled": cfg.enabled, "config": cfg.config}
    for field_name in ("enabled", "timeout_ms", "max_retries", "reliability_prior",
                       "rate_limit_per_minute", "config"):
        value = getattr(body, field_name)
        if value is not None:
            setattr(cfg, field_name, value)
    audit.record(db, principal.tenant_id, action="provider.update", object_type="provider",
                 object_id=cfg.id, actor_user_id=principal.user_id, actor_type="user",
                 before=before, after=body.model_dump(exclude_none=True))
    db.commit()
    get_registry(db, refresh=True)
    return {"ok": True}


# ── Routing policies ────────────────────────────────────────────────────────

class RoutingPolicyIn(BaseModel):
    name: str = Field(max_length=120)
    document: dict | None = None
    yaml_document: str | None = Field(default=None, max_length=20000)
    activate: bool = True


@router.get("/routing-policies")
def list_routing_policies(
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(RoutingPolicy).where(RoutingPolicy.tenant_id == principal.tenant_id)
    ).scalars().all()
    return [
        {"id": r.id, "name": r.name, "version": r.version, "is_active": r.is_active,
         "document": r.document, "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]


@router.post("/routing-policies", status_code=status.HTTP_201_CREATED)
def create_routing_policy(
    body: RoutingPolicyIn,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    if body.document is None and body.yaml_document is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "document or yaml_document required")
    document = body.document
    if document is None:
        try:
            document = yaml.safe_load(body.yaml_document or "")
        except yaml.YAMLError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"invalid YAML: {exc}") from exc
    if not isinstance(document, dict) or "fields" not in document:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "policy must be a mapping with a 'fields' section")
    existing = db.execute(
        select(RoutingPolicy).where(
            RoutingPolicy.tenant_id == principal.tenant_id, RoutingPolicy.name == body.name
        )
    ).scalar_one_or_none()
    if existing:
        existing.version += 1
        existing.document = document
        existing.is_active = body.activate
        row = existing
    else:
        row = RoutingPolicy(
            tenant_id=principal.tenant_id, name=body.name, document=document,
            is_active=body.activate,
        )
        db.add(row)
    audit.record(db, principal.tenant_id, action="routing_policy.upsert", object_type="routing_policy",
                 object_id=row.id, actor_user_id=principal.user_id, actor_type="user",
                 after={"name": body.name, "version": row.version})
    db.commit()
    return {"id": row.id, "name": row.name, "version": row.version}


# ── Staleness policies ──────────────────────────────────────────────────────

class StalenessPolicyIn(BaseModel):
    entity_type: str = Field(pattern="^(contact|account)$")
    field_name: str = Field(max_length=80)
    fresh_days: int = Field(ge=1, le=3650)
    aging_days: int = Field(ge=1, le=3650)
    stale_days: int = Field(ge=1, le=3650)


@router.get("/staleness-policies")
def list_staleness_policies(
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(StalenessPolicy).where(
            (StalenessPolicy.tenant_id == principal.tenant_id) | (StalenessPolicy.tenant_id.is_(None))
        )
    ).scalars().all()
    return [
        {"id": r.id, "tenant_id": r.tenant_id, "entity_type": r.entity_type,
         "field_name": r.field_name, "fresh_days": r.fresh_days, "aging_days": r.aging_days,
         "stale_days": r.stale_days, "scope": "tenant" if r.tenant_id else "global"}
        for r in rows
    ]


@router.put("/staleness-policies")
def upsert_staleness_policy(
    body: StalenessPolicyIn,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    if not (body.fresh_days <= body.aging_days <= body.stale_days):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "must satisfy fresh_days <= aging_days <= stale_days")
    row = db.execute(
        select(StalenessPolicy).where(
            StalenessPolicy.tenant_id == principal.tenant_id,
            StalenessPolicy.entity_type == body.entity_type,
            StalenessPolicy.field_name == body.field_name,
        )
    ).scalar_one_or_none()
    if row is None:
        row = StalenessPolicy(tenant_id=principal.tenant_id, **body.model_dump())
        db.add(row)
    else:
        row.fresh_days, row.aging_days, row.stale_days = body.fresh_days, body.aging_days, body.stale_days
    audit.record(db, principal.tenant_id, action="staleness_policy.upsert",
                 object_type="staleness_policy", object_id=row.id,
                 actor_user_id=principal.user_id, actor_type="user",
                 after=body.model_dump())
    db.commit()
    return {"id": row.id}


# ── Campaigns & budgets ─────────────────────────────────────────────────────

class CampaignIn(BaseModel):
    name: str = Field(max_length=200)
    filters: dict = Field(default_factory=dict)
    required_fields: list[str] = Field(default_factory=list)
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    crm_write_enabled: bool = True
    routing_policy_id: str | None = None


@router.get("/campaigns")
def list_campaigns(
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(Campaign).where(Campaign.tenant_id == principal.tenant_id)
    ).scalars().all()
    budgets = db.execute(
        select(Budget).where(Budget.tenant_id == principal.tenant_id)
    ).scalars().all()
    by_campaign: dict[str | None, list[Budget]] = {}
    for b in budgets:
        by_campaign.setdefault(b.campaign_id, []).append(b)
    return [
        {
            "id": c.id, "name": c.name, "status": c.status, "filters": c.filters,
            "required_fields": c.required_fields, "min_confidence": c.min_confidence,
            "crm_write_enabled": c.crm_write_enabled, "routing_policy_id": c.routing_policy_id,
            "budgets": [
                {
                    "id": b.id, "name": b.name, "kind": b.kind, "period": b.period,
                    "limit_credits": float(b.limit_credits), "spent_credits": float(b.spent_credits),
                    "reserved_credits": float(b.reserved_credits),
                    "warning_threshold": b.warning_threshold,
                    "degradation_mode": b.degradation_mode, "is_active": b.is_active,
                }
                for b in by_campaign.get(c.id, [])
            ],
        }
        for c in rows
    ]


@router.post("/campaigns", status_code=status.HTTP_201_CREATED)
def create_campaign(
    body: CampaignIn,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    row = Campaign(tenant_id=principal.tenant_id, **body.model_dump())
    db.add(row)
    audit.record(db, principal.tenant_id, action="campaign.create", object_type="campaign",
                 object_id=row.id, actor_user_id=principal.user_id, actor_type="user",
                 after=body.model_dump())
    db.commit()
    return {"id": row.id}


class BudgetIn(BaseModel):
    name: str = Field(max_length=200)
    campaign_id: str | None = None
    kind: BudgetKind = BudgetKind.HARD
    period: BudgetPeriod = BudgetPeriod.LIFETIME
    limit_credits: float = Field(gt=0)
    warning_threshold: float = Field(default=0.8, ge=0, le=1)
    per_record_max: float | None = Field(default=None, gt=0)
    per_field_max: float | None = Field(default=None, gt=0)
    degradation_mode: str = Field(default="cheapest",
                                  pattern="^(cache_only|cheapest|required_fields_only|stop)$")


@router.post("/budgets", status_code=status.HTTP_201_CREATED)
def create_budget(
    body: BudgetIn,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    if body.campaign_id:
        campaign = db.get(Campaign, body.campaign_id)
        if campaign is None or campaign.tenant_id != principal.tenant_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not found")
    row = Budget(
        tenant_id=principal.tenant_id,
        **{**body.model_dump(), "kind": body.kind.value, "period": body.period.value},
    )
    db.add(row)
    audit.record(db, principal.tenant_id, action="budget.create", object_type="budget",
                 object_id=row.id, actor_user_id=principal.user_id, actor_type="user",
                 after=body.model_dump(mode="json"))
    db.commit()
    return {"id": row.id}


# ── Suppressions ────────────────────────────────────────────────────────────

class SuppressionIn(BaseModel):
    kind: str = Field(pattern="^(domain|email|company_name)$")
    value: str = Field(max_length=320)
    reason: str | None = Field(default=None, max_length=300)


@router.get("/suppressions")
def list_suppressions(
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(Suppression).where(Suppression.tenant_id == principal.tenant_id)
    ).scalars().all()
    return [{"id": r.id, "kind": r.kind, "value": r.value, "reason": r.reason} for r in rows]


@router.post("/suppressions", status_code=status.HTTP_201_CREATED)
def create_suppression(
    body: SuppressionIn,
    principal: Principal = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    row = Suppression(tenant_id=principal.tenant_id, kind=body.kind,
                      value=body.value.lower().strip(), reason=body.reason)
    db.add(row)
    audit.record(db, principal.tenant_id, action="suppression.create", object_type="suppression",
                 object_id=row.id, actor_user_id=principal.user_id, actor_type="user",
                 after=body.model_dump())
    db.commit()
    return {"id": row.id}
