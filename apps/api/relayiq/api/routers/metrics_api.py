"""Cost & quality analytics endpoints — every number is derived from persisted rows."""

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from relayiq.api.deps import Principal, require_analyst
from relayiq.db import get_db
from relayiq.models import CostLedgerEntry, EnrichmentJob, ProviderRequest
from relayiq.services import ledger as ledger_service
from relayiq.services import quality as quality_service
from relayiq.services import review as review_service
from relayiq.services.provider_exec import provider_stats

router = APIRouter(prefix="/v1/metrics", tags=["metrics"])


@router.get("/overview")
def overview(
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    """The primary product metrics: cost per usable lead, fill rate, redundant-call rate,
    conflict rate, reviewer acceptance, p95 latency, CRM sync failure rate."""
    tenant = principal.tenant_id
    cost = ledger_service.cost_per(db, tenant)
    summary = ledger_service.cost_summary(db, tenant)
    q = quality_service.quality_summary(db, tenant)
    review = review_service.queue_metrics(db, tenant)

    total_entries, cache_hits = db.execute(
        select(
            func.count(CostLedgerEntry.id),
            func.coalesce(
                func.sum(
                    case((CostLedgerEntry.cache_status.in_(["hit", "stale_hit"]), 1), else_=0)
                ), 0),
        ).where(CostLedgerEntry.tenant_id == tenant,
                CostLedgerEntry.operation == "enrich_field")
    ).one()

    latencies = [
        r for (r,) in db.execute(
            select(ProviderRequest.latency_ms).where(
                ProviderRequest.tenant_id == tenant, ProviderRequest.latency_ms.isnot(None)
            )
        ).all()
    ]
    latencies.sort()

    def pct(p: float) -> float | None:
        return latencies[min(len(latencies) - 1, int(p * len(latencies)))] if latencies else None

    total_entries = int(total_entries or 0)
    cache_hits = int(cache_hits or 0)
    return {
        "records_processed": cost["attempted_records"],
        "accepted_records": cost["accepted_records"],
        "usable_leads": cost["usable_leads"],
        "total_cost_credits": cost["total_cost_credits"],
        "cost_per_usable_lead": cost["cost_per_usable_lead"],
        "cost_per_accepted_record": cost["cost_per_accepted_record"],
        "fill_rate": q["fill_rate"],
        "cache_hit_rate": round(cache_hits / total_entries, 4) if total_entries else None,
        "redundant_call_rate": round(cache_hits / total_entries, 4) if total_entries else None,
        "redundant_cost_avoided_credits": summary["redundant_cost_avoided_credits"],
        "conflict_rate": q["conflict_rate"],
        "review_acceptance_rate": review["acceptance_rate"],
        "review_pending": review["pending"],
        "p50_provider_latency_ms": pct(0.5),
        "p95_provider_latency_ms": pct(0.95),
        "crm_sync_failure_rate": q["crm_sync_failure_rate"],
        "spend_on_rejected_records_credits": summary["spend_on_rejected_records_credits"],
        "spend_on_stale_credits": summary["spend_on_stale_credits"],
    }


@router.get("/cost")
def cost_metrics(
    campaign_id: str | None = None,
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    tenant = principal.tenant_id
    return {
        **ledger_service.cost_per(db, tenant, campaign_id),
        **ledger_service.cost_summary(db, tenant, campaign_id),
        "by_provider": ledger_service.spend_by(db, tenant, "provider"),
        "by_campaign": ledger_service.spend_by(db, tenant, "campaign"),
        "by_operation": ledger_service.spend_by(db, tenant, "operation"),
        "by_field": ledger_service.spend_by_field(db, tenant),
    }


@router.get("/quality")
def quality_metrics(
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    return {
        **quality_service.quality_summary(db, principal.tenant_id),
        "provider_field_quality": quality_service.provider_field_quality(db, principal.tenant_id),
    }


@router.get("/providers")
def provider_metrics(
    hours: int = 24,
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    keys = [
        k for (k,) in db.execute(select(ProviderRequest.provider_key).distinct()).all()
    ]
    return [provider_stats(db, k, hours=hours) for k in sorted(keys)]


@router.get("/campaigns/{campaign_id}/economics")
def campaign_economics(
    campaign_id: str,
    principal: Principal = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    from relayiq.models import Budget

    tenant = principal.tenant_id
    cost = ledger_service.cost_per(db, tenant, campaign_id)
    summary = ledger_service.cost_summary(db, tenant, campaign_id)
    budgets = db.execute(
        select(Budget).where(Budget.tenant_id == tenant, Budget.campaign_id == campaign_id)
    ).scalars().all()
    prevented = db.execute(
        select(func.count(EnrichmentJob.id)).where(
            EnrichmentJob.tenant_id == tenant,
            EnrichmentJob.campaign_id == campaign_id,
            EnrichmentJob.pre_decision.in_(["reject", "skip", "policy_block", "budget_block"]),
        )
    ).scalar_one()
    return {
        **cost,
        **summary,
        "budgets": [
            {
                "id": b.id, "name": b.name, "kind": b.kind, "period": b.period,
                "limit_credits": float(b.limit_credits),
                "spent_credits": float(b.spent_credits),
                "reserved_credits": float(b.reserved_credits),
                "remaining_credits": float(b.limit_credits) - float(b.spent_credits) - float(b.reserved_credits),  # noqa: E501
                "variance_credits": float(b.limit_credits) - float(b.spent_credits),
                "warning_threshold": b.warning_threshold,
            }
            for b in budgets
        ],
        "enrichment_prevented_by_filters": int(prevented),
    }
