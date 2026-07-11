"""Pre-enrichment decision engine: decide *whether to spend* before any provider is called.

Rejected/skipped/cached records must consume zero provider credits — the ledger proves it.
Checks run cheapest-first; the first terminal check wins, remaining reasons still recorded
where helpful for operators.
"""

from dataclasses import dataclass, field as dc_field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.canonical.normalize import (
    extract_root_domain,
    is_valid_domain,
    is_valid_email_syntax,
)
from relayiq.enums import EntityType, JobStatus, PreDecision, StalenessState
from relayiq.models import Campaign, CanonicalFieldValue, EnrichmentJob
from relayiq.services import policy as policy_service
from relayiq.services import staleness as staleness_service
from relayiq.services.budget import BudgetState


@dataclass
class DecisionInput:
    tenant_id: str
    entity_type: str
    entity_id: str
    requested_fields: list[str]
    identifiers: dict  # work_email / root_domain / name parts as provided
    campaign: Campaign | None
    budget_state: BudgetState
    providers_available: bool
    estimated_min_cost: float


@dataclass
class DecisionOutput:
    decision: PreDecision
    reasons: list[str]
    fields_to_enrich: list[str] = dc_field(default_factory=list)
    fields_from_cache: dict = dc_field(default_factory=dict)  # field -> canonical value info
    job_status: str = JobStatus.RUNNING.value


def _fresh_canonical_fields(
    session: Session, tenant_id: str, entity_type: str, entity_id: str, fields: list[str]
) -> dict[str, dict]:
    rows = session.execute(
        select(CanonicalFieldValue).where(
            CanonicalFieldValue.tenant_id == tenant_id,
            CanonicalFieldValue.entity_type == entity_type,
            CanonicalFieldValue.entity_id == entity_id,
            CanonicalFieldValue.field_name.in_(fields),
        )
    ).scalars().all()
    now = datetime.now(UTC)
    fresh: dict[str, dict] = {}
    for row in rows:
        if row.value is None:
            continue
        state = staleness_service.classify(
            session, tenant_id, entity_type, row.field_name,
            verified_at=row.last_verified_at, now=now,
        )
        if staleness_service.is_reusable(state):
            fresh[row.field_name] = {
                "value": row.value,
                "normalized_value": row.normalized_value,
                "confidence": row.confidence,
                "staleness": state.value,
                "source": "canonical_store",
            }
    return fresh


def _campaign_filter_reasons(campaign: Campaign | None, identifiers: dict) -> list[str]:
    if campaign is None:
        return []
    f = campaign.filters or {}
    reasons = []
    allowed_countries = f.get("allowed_countries")
    country = identifiers.get("country") or identifiers.get("hq_country")
    if allowed_countries and country and country not in allowed_countries:
        reasons.append(f"country '{country}' not in campaign allowlist")
    min_emp = f.get("min_employee_count")
    emp = identifiers.get("employee_count")
    if min_emp is not None and emp is not None:
        try:
            if int(emp) < int(min_emp):
                reasons.append(f"employee_count {emp} below campaign minimum {min_emp}")
        except (TypeError, ValueError):
            pass
    required_ids = f.get("required_identifiers", [])
    for rid in required_ids:
        if not identifiers.get(rid):
            reasons.append(f"campaign requires identifier '{rid}'")
    return reasons


def decide(session: Session, inp: DecisionInput) -> DecisionOutput:
    reasons: list[str] = []
    email = inp.identifiers.get("work_email")
    domain = inp.identifiers.get("root_domain") or extract_root_domain(inp.identifiers.get("website"))
    company_name = inp.identifiers.get("company_name") or inp.identifiers.get("name")

    # 1. Permitted-use policy & suppressions (never spend on suppressed records)
    pol = policy_service.evaluate(
        session, inp.tenant_id,
        entity_type=inp.entity_type, entity_id=inp.entity_id,
        requested_fields=inp.requested_fields,
        domain=domain, email=email, company_name=company_name,
        campaign_restrictions=(inp.campaign.filters if inp.campaign else None),
    )
    if not pol.allowed:
        return DecisionOutput(PreDecision.POLICY_BLOCK, pol.reasons, job_status=JobStatus.BLOCKED_POLICY.value)
    requested = [f for f in inp.requested_fields if f not in set(pol.blocked_fields)]
    if pol.blocked_fields:
        reasons.append(f"dropped policy-restricted fields: {sorted(pol.blocked_fields)}")
    if not requested:
        return DecisionOutput(
            PreDecision.POLICY_BLOCK, [*reasons, "no requested fields permitted by policy"],
            job_status=JobStatus.BLOCKED_POLICY.value,
        )

    # 2. Required identifiers
    if inp.entity_type == EntityType.CONTACT.value:
        has_identity = bool(email) or (
            bool(inp.identifiers.get("full_name") or inp.identifiers.get("last_name")) and bool(domain)
        )
        if not has_identity:
            return DecisionOutput(
                PreDecision.REJECT,
                [*reasons, "contact needs a work email, or a name plus company domain, to be enrichable"],
                job_status=JobStatus.REJECTED.value,
            )
    else:
        if not domain and not company_name:
            return DecisionOutput(
                PreDecision.REJECT, [*reasons, "account needs a domain or company name"],
                job_status=JobStatus.REJECTED.value,
            )

    # 3. Syntax validity — spending credits on malformed identifiers is waste
    if email and not is_valid_email_syntax(email):
        return DecisionOutput(
            PreDecision.REJECT, [*reasons, f"work email failed syntax validation"],
            job_status=JobStatus.REJECTED.value,
        )
    raw_domainish = inp.identifiers.get("root_domain")
    if raw_domainish and not domain:
        return DecisionOutput(
            PreDecision.REJECT, [*reasons, "company domain is not a valid domain"],
            job_status=JobStatus.REJECTED.value,
        )
    if domain and not is_valid_domain(domain):
        return DecisionOutput(
            PreDecision.REJECT, [*reasons, f"company domain '{domain}' is not a valid domain"],
            job_status=JobStatus.REJECTED.value,
        )

    # 4. Campaign filters
    campaign_reasons = _campaign_filter_reasons(inp.campaign, inp.identifiers)
    if campaign_reasons:
        return DecisionOutput(
            PreDecision.SKIP, [*reasons, *campaign_reasons], job_status=JobStatus.SKIPPED.value
        )

    # 5. Duplicate in-flight work on the same entity (cheap dedupe; idempotency keys
    #    handle exact duplicates — this catches concurrent different-key submissions)
    inflight = session.execute(
        select(EnrichmentJob.id).where(
            EnrichmentJob.tenant_id == inp.tenant_id,
            EnrichmentJob.entity_type == inp.entity_type,
            EnrichmentJob.entity_id == inp.entity_id,
            EnrichmentJob.status == JobStatus.RUNNING.value,
        ).limit(1)
    ).scalar_one_or_none()
    if inflight:
        return DecisionOutput(
            PreDecision.SKIP, [*reasons, f"another enrichment job ({inflight}) is already running for this entity"],
            job_status=JobStatus.SKIPPED.value,
        )

    # 6. Existing fresh canonical fields → serve from store, enrich only the gaps
    fresh = _fresh_canonical_fields(session, inp.tenant_id, inp.entity_type, inp.entity_id, requested)
    to_enrich = [f for f in requested if f not in fresh]
    if not to_enrich:
        return DecisionOutput(
            PreDecision.USE_CACHE,
            [*reasons, f"all {len(requested)} requested field(s) are fresh in the canonical store"],
            fields_from_cache=fresh,
            job_status=JobStatus.COMPLETED_CACHED.value,
        )
    if fresh:
        reasons.append(f"{len(fresh)} field(s) served from canonical store; enriching {len(to_enrich)}")

    # 7. Budget
    if not inp.budget_state.allowed:
        return DecisionOutput(
            PreDecision.BUDGET_BLOCK,
            [*reasons, f"budget check failed: {inp.budget_state.reason} "
                       f"(estimated cost {inp.estimated_min_cost:.2f} credits)"],
            fields_from_cache=fresh,
            job_status=JobStatus.BLOCKED_BUDGET.value,
        )

    # 8. Provider availability
    if not inp.providers_available:
        return DecisionOutput(
            PreDecision.SKIP, [*reasons, "no enabled provider is currently available (circuits open?)"],
            fields_from_cache=fresh, job_status=JobStatus.SKIPPED.value,
        )

    reasons.append(f"enriching {len(to_enrich)} field(s): {sorted(to_enrich)}")
    if inp.budget_state.warning:
        reasons.append(
            f"budget warning threshold crossed — degradation mode '{inp.budget_state.degradation_mode}'"
        )
    return DecisionOutput(
        PreDecision.ENRICH, reasons, fields_to_enrich=to_enrich, fields_from_cache=fresh
    )
