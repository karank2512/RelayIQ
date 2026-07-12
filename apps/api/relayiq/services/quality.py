"""Usable-lead definition and data-quality metrics.

The usable-lead definition is configurable (relayiq/config.py: usable_lead_*); the default
requires: matched company, valid domain, contact name, accepted title-or-seniority, minimum
entity confidence, no blocking conflict, no suppression, and CRM-sync eligibility.
Every metric here is computed from persisted rows — nothing is hardcoded.
"""

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from relayiq.canonical.normalize import is_valid_domain
from relayiq.config import get_settings
from relayiq.enums import EntityType, ReconciliationOutcome, ReviewTaskStatus, SyncStatus
from relayiq.models import (
    Account,
    CanonicalFieldValue,
    Contact,
    CrmSyncAttempt,
    EnrichmentJob,
    FieldObservation,
    ReconciliationDecision,
    ReviewTask,
)


def _canon(session: Session, tenant_id: str, entity_type: str, entity_id: str) -> dict[str, CanonicalFieldValue]:  # noqa: E501
    rows = session.execute(
        select(CanonicalFieldValue).where(
            CanonicalFieldValue.tenant_id == tenant_id,
            CanonicalFieldValue.entity_type == entity_type,
            CanonicalFieldValue.entity_id == entity_id,
        )
    ).scalars().all()
    return {r.field_name: r for r in rows}


def evaluate_usable_lead(
    session: Session,
    tenant_id: str,
    contact: Contact,
    *,
    entity_confidence: float | None,
    suppressed: bool = False,
    sync_eligible: bool = True,
) -> tuple[bool, list[str]]:
    """Returns (usable, failed_criteria). Criteria are configurable via settings."""
    s = get_settings()
    failures: list[str] = []
    canon = _canon(session, tenant_id, EntityType.CONTACT.value, contact.id)

    if s.usable_lead_require_company and not (contact.account_id or contact.company_domain):
        failures.append("no matched company")
    domain = contact.company_domain
    if contact.account_id:
        account = session.get(Account, contact.account_id)
        domain = domain or (account.root_domain if account else None)
    if s.usable_lead_require_valid_domain and not (domain and is_valid_domain(domain)):
        failures.append("no valid company domain")
    if s.usable_lead_require_contact_name and not (contact.full_name or contact.last_name):
        failures.append("no contact name")

    if s.usable_lead_require_title_or_seniority:
        title_row = canon.get("job_title")
        seniority_row = canon.get("seniority")
        title_ok = (title_row and title_row.value and title_row.staleness_state
                    in ("fresh", "aging")) or (contact.job_title and not canon)
        seniority_ok = seniority_row and seniority_row.value
        if not (title_ok or seniority_ok):
            failures.append("no accepted current job title or seniority")

    if entity_confidence is not None and entity_confidence < s.usable_lead_min_confidence:
        failures.append(
            f"entity confidence {entity_confidence:.2f} below {s.usable_lead_min_confidence:.2f}"
        )
    blocking = session.execute(
        select(func.count(ReviewTask.id)).where(
            ReviewTask.tenant_id == tenant_id,
            ReviewTask.entity_type == EntityType.CONTACT.value,
            ReviewTask.entity_id == contact.id,
            ReviewTask.status == ReviewTaskStatus.PENDING.value,
        )
    ).scalar_one()
    if blocking:
        failures.append(f"{blocking} unresolved review task(s)")
    if suppressed:
        failures.append("suppressed by policy")
    if not sync_eligible:
        failures.append("not eligible for CRM sync")
    return (not failures, failures)


def quality_summary(session: Session, tenant_id: str) -> dict:
    """Fill rate, conflict rate, disagreement, staleness, review and sync rates — formulas in
    docs/benchmarks/metric-definitions.md."""
    obs_total = session.execute(
        select(func.count(FieldObservation.id)).where(FieldObservation.tenant_id == tenant_id)
    ).scalar_one()
    jobs = session.execute(
        select(EnrichmentJob).where(EnrichmentJob.tenant_id == tenant_id)
    ).scalars().all()
    enriching_jobs = [j for j in jobs if j.pre_decision == "enrich"]
    requested = sum(len(j.requested_fields or []) for j in enriching_jobs)
    filled = sum(int(j.result_summary.get("fields_filled", 0)) for j in enriching_jobs)

    recon = session.execute(
        select(ReconciliationDecision.outcome, func.count(ReconciliationDecision.id))
        .where(ReconciliationDecision.tenant_id == tenant_id)
        .group_by(ReconciliationDecision.outcome)
    ).all()
    recon_counts = {k: int(v) for k, v in recon}
    recon_total = sum(recon_counts.values())
    conflicts = sum(
        recon_counts.get(o, 0)
        for o in (
            ReconciliationOutcome.REQUIRE_REVIEW.value,
            ReconciliationOutcome.ACCEPT_WITH_WARNING.value,
            ReconciliationOutcome.RETAIN_CRM.value,
        )
    )

    canon_rows = session.execute(
        select(CanonicalFieldValue.staleness_state, func.count(CanonicalFieldValue.id))
        .where(CanonicalFieldValue.tenant_id == tenant_id)
        .group_by(CanonicalFieldValue.staleness_state)
    ).all()
    staleness_dist = {k or "unknown": int(v) for k, v in canon_rows}
    canon_total = sum(staleness_dist.values())

    syncs = session.execute(
        select(CrmSyncAttempt.status, func.count(CrmSyncAttempt.id))
        .where(CrmSyncAttempt.tenant_id == tenant_id)
        .group_by(CrmSyncAttempt.status)
    ).all()
    sync_counts = {k: int(v) for k, v in syncs}
    sync_total = sum(sync_counts.values())
    sync_failed = sync_counts.get(SyncStatus.FAILED.value, 0)

    usable = len([j for j in jobs if j.result_summary.get("usable_lead")])
    return {
        "observations": int(obs_total),
        "jobs_total": len(jobs),
        "jobs_enriched": len(enriching_jobs),
        "fill_rate": round(filled / requested, 4) if requested else None,
        "conflict_rate": round(conflicts / recon_total, 4) if recon_total else None,
        "reconciliation_outcomes": recon_counts,
        "staleness_distribution": staleness_dist,
        "stale_share": round(
            (staleness_dist.get("stale", 0) + staleness_dist.get("expired", 0)) / canon_total, 4
        ) if canon_total else None,
        "usable_leads": usable,
        "crm_sync_attempts": sync_total,
        "crm_sync_failure_rate": round(sync_failed / sync_total, 4) if sync_total else None,
        "crm_sync_outcomes": sync_counts,
    }


def provider_field_quality(session: Session, tenant_id: str) -> list[dict]:
    """Per provider x field: fill volume, selection share, review precision."""
    rows = session.execute(
        select(
            FieldObservation.provider_key,
            FieldObservation.field_name,
            func.count(FieldObservation.id),
            func.sum(case((FieldObservation.is_selected, 1), else_=0)),
            func.sum(case((FieldObservation.is_rejected, 1), else_=0)),
        )
        .where(FieldObservation.tenant_id == tenant_id)
        .group_by(FieldObservation.provider_key, FieldObservation.field_name)
    ).all()
    out = []
    for provider, field_name, total, selected, rejected in rows:
        total = int(total or 0)
        out.append({
            "provider": provider,
            "field": field_name,
            "observations": total,
            "selected_share": round(int(selected or 0) / total, 4) if total else None,
            "rejected_share": round(int(rejected or 0) / total, 4) if total else None,
        })
    return sorted(out, key=lambda r: (r["provider"] or "", r["field"]))
