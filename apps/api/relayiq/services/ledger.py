"""Cost ledger: one entry per attempted cost-bearing operation (rule 12) plus cache-avoidance
entries so 'redundant cost avoided' is measurable, not estimated.

Metric definitions live in docs/benchmarks/metric-definitions.md; the SQL here implements them.
"""

from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from relayiq.models import CostLedgerEntry, EnrichmentJob
from relayiq.observability.metrics import PROVIDER_COST


def record_entry(
    session: Session,
    *,
    tenant_id: str,
    operation: str,
    campaign_id: str | None = None,
    job_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    provider_key: str | None = None,
    provider_request_id: str | None = None,
    fields_requested: list[str] | None = None,
    estimated_cost: float = 0.0,
    actual_cost: float = 0.0,
    outcome: str | None = None,
    cache_status: str | None = None,
    was_redundant: bool = False,
    avoided_cost: float = 0.0,
    spent_on_stale: bool = False,
    trace_id: str | None = None,
    commit: bool = False,
) -> CostLedgerEntry:
    entry = CostLedgerEntry(
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        job_id=job_id,
        entity_type=entity_type,
        entity_id=entity_id,
        provider_key=provider_key,
        provider_request_id=provider_request_id,
        operation=operation,
        fields_requested=fields_requested or [],
        estimated_cost_credits=round(estimated_cost, 4),
        actual_cost_credits=round(actual_cost, 4),
        outcome=outcome,
        cache_status=cache_status,
        was_redundant=was_redundant,
        avoided_cost_credits=round(avoided_cost, 4),
        spent_on_stale=spent_on_stale,
        trace_id=trace_id,
    )
    session.add(entry)
    if provider_key and actual_cost:
        PROVIDER_COST.labels(provider=provider_key).inc(actual_cost)
    if commit:
        session.commit()
    return entry


def mark_acceptance(session: Session, job_id: str, accepted_by_provider: dict[str, bool]) -> None:
    """After reconciliation: flag which providers' spend produced accepted values."""
    entries = session.execute(
        select(CostLedgerEntry).where(CostLedgerEntry.job_id == job_id, CostLedgerEntry.provider_key.isnot(None))  # noqa: E501
    ).scalars()
    for e in entries:
        if e.provider_key in accepted_by_provider:
            e.result_accepted = accepted_by_provider[e.provider_key]


def mark_record_rejected(session: Session, job_id: str) -> None:
    entries = session.execute(select(CostLedgerEntry).where(CostLedgerEntry.job_id == job_id)).scalars()
    for e in entries:
        e.record_rejected_later = True


# ── Aggregations ────────────────────────────────────────────────────────────

def _dec(x) -> float:
    return float(x if x is not None else 0)


def cost_summary(session: Session, tenant_id: str, campaign_id: str | None = None) -> dict:
    q = select(
        func.coalesce(func.sum(CostLedgerEntry.actual_cost_credits), 0),
        func.coalesce(func.sum(CostLedgerEntry.avoided_cost_credits), 0),
        func.coalesce(func.sum(case((CostLedgerEntry.was_redundant, CostLedgerEntry.actual_cost_credits), else_=0)), 0),  # noqa: E501
        func.coalesce(func.sum(case((CostLedgerEntry.spent_on_stale, CostLedgerEntry.actual_cost_credits), else_=0)), 0),  # noqa: E501
        func.coalesce(func.sum(
            case((CostLedgerEntry.record_rejected_later, CostLedgerEntry.actual_cost_credits), else_=0)), 0),
        func.count(CostLedgerEntry.id),
    ).where(CostLedgerEntry.tenant_id == tenant_id)
    if campaign_id:
        q = q.where(CostLedgerEntry.campaign_id == campaign_id)
    total, avoided, redundant, stale, rejected, entries = session.execute(q).one()
    return {
        "total_spend_credits": _dec(total),
        "redundant_cost_avoided_credits": _dec(avoided),
        "redundant_spend_credits": _dec(redundant),
        "spend_on_stale_credits": _dec(stale),
        "spend_on_rejected_records_credits": _dec(rejected),
        "ledger_entries": int(entries),
    }


def spend_by(session: Session, tenant_id: str, dimension: str) -> list[dict]:
    col = {
        "provider": CostLedgerEntry.provider_key,
        "campaign": CostLedgerEntry.campaign_id,
        "workflow": CostLedgerEntry.job_id,
        "operation": CostLedgerEntry.operation,
    }[dimension]
    rows = session.execute(
        select(col, func.sum(CostLedgerEntry.actual_cost_credits), func.count(CostLedgerEntry.id))
        .where(CostLedgerEntry.tenant_id == tenant_id)
        .group_by(col)
    ).all()
    return [{"key": r[0], "spend_credits": _dec(r[1]), "entries": int(r[2])} for r in rows if r[0]]


def spend_by_field(session: Session, tenant_id: str) -> list[dict]:
    """Field-level spend from per-field ledger entries (operation = enrich_field)."""
    rows = session.execute(
        select(
            CostLedgerEntry.fields_requested,
            CostLedgerEntry.actual_cost_credits,
        ).where(CostLedgerEntry.tenant_id == tenant_id, CostLedgerEntry.operation == "enrich_field")
    ).all()
    agg: dict[str, Decimal] = {}
    for fields, cost in rows:
        for f in fields or []:
            agg[f] = agg.get(f, Decimal(0)) + Decimal(str(cost))
    return [{"key": k, "spend_credits": float(v)} for k, v in sorted(agg.items())]


def cost_per(session: Session, tenant_id: str, campaign_id: str | None = None) -> dict:
    """Cost-per-X metrics. Denominator definitions (docs/benchmarks/metric-definitions.md):
    attempted = jobs that reached the pipeline; accepted = jobs whose result auto-accepted or
    review-accepted; usable leads = jobs whose entity satisfies the usable-lead definition
    (computed by quality service and stamped on the job result_summary)."""
    jq = select(EnrichmentJob).where(EnrichmentJob.tenant_id == tenant_id)
    if campaign_id:
        jq = jq.where(EnrichmentJob.campaign_id == campaign_id)
    jobs = session.execute(jq).scalars().all()
    attempted = [j for j in jobs if j.status not in ("received",)]
    total_cost = sum(Decimal(str(j.actual_cost_credits)) for j in attempted)
    accepted = [j for j in attempted if j.result_summary.get("accepted")]
    complete = [j for j in attempted if j.result_summary.get("all_requested_fields_filled")]
    usable = [j for j in attempted if j.result_summary.get("usable_lead")]

    def per(n: int) -> float | None:
        return round(float(total_cost) / n, 4) if n else None

    return {
        "total_cost_credits": float(total_cost),
        "attempted_records": len(attempted),
        "accepted_records": len(accepted),
        "complete_records": len(complete),
        "usable_leads": len(usable),
        "cost_per_attempted_record": per(len(attempted)),
        "cost_per_accepted_record": per(len(accepted)),
        "cost_per_complete_record": per(len(complete)),
        "cost_per_usable_lead": per(len(usable)),
    }
