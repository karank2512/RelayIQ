"""Field-level provider routing (ADR-004).

Policy documents (YAML at the API edge, JSON in the DB) configure routing without code
changes:

    version: 1
    defaults:
      strategy: balanced          # cheapest_capable | quality_first | balanced | dynamic
      fallback: true
      max_candidates: 3
    fields:
      contact.job_title:  {providers: [beta, alpha], strategy: quality_first}
      account.root_domain: {providers: [alpha, beta], strategy: cheapest_capable}

Strategies score candidates transparently; every factor lands in routing_decisions so any
selection is explainable after the fact.

`dynamic` (phase 3) blends live historical performance — reviewed precision, fill rate,
health, p95 latency — with cost; the other strategies use static priors only.
"""

from dataclasses import dataclass, field as dc_field

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from relayiq.models import FieldObservation, ProviderHealthWindow
from relayiq.providers.base import ProviderAdapter
from relayiq.providers.registry import ProviderRegistry

DEFAULT_POLICY: dict = {
    "version": 1,
    "defaults": {"strategy": "balanced", "fallback": True, "max_candidates": 3},
    "fields": {
        # Alpha: cheap + strong on firmographics. Beta: fresh titles/seniority.
        "account.root_domain": {"providers": ["alpha", "beta"], "strategy": "cheapest_capable"},
        "account.website": {"providers": ["alpha", "beta"], "strategy": "cheapest_capable"},
        "account.employee_count": {"providers": ["alpha", "beta"], "strategy": "cheapest_capable"},
        "account.industry": {"providers": ["alpha", "beta"], "strategy": "balanced"},
        "contact.job_title": {"providers": ["beta", "alpha"], "strategy": "quality_first"},
        "contact.seniority": {"providers": ["beta", "alpha"], "strategy": "quality_first"},
        "contact.department": {"providers": ["beta", "alpha"], "strategy": "quality_first"},
        "contact.work_email": {"providers": ["alpha", "beta"], "strategy": "balanced"},
        "contact.linkedin_url": {"providers": ["beta", "alpha"], "strategy": "quality_first"},
    },
}

# Static field-quality priors per provider (mirrors simulator personalities; used by
# non-dynamic strategies and as the dynamic strategy's prior when history is thin).
FIELD_QUALITY_PRIORS: dict[str, dict[str, float]] = {
    "alpha": {
        "root_domain": 0.92, "website": 0.9, "employee_count": 0.9, "industry": 0.88,
        "job_title": 0.7, "seniority": 0.7, "department": 0.68, "work_email": 0.8,
        "_default": 0.82,
    },
    "beta": {
        "job_title": 0.93, "seniority": 0.92, "department": 0.9, "linkedin_url": 0.9,
        "work_email": 0.88, "root_domain": 0.82, "employee_count": 0.8, "industry": 0.8,
        "_default": 0.84,
    },
}


@dataclass
class Candidate:
    provider_key: str
    cost: float
    quality: float
    health_penalty: float
    p95_latency_ms: float | None
    score: float
    factors: dict


@dataclass
class FieldRoute:
    field_name: str
    strategy: str
    candidates: list[Candidate]
    selected: str | None
    rejected: list[dict]
    expected_cost: float
    factors: dict = dc_field(default_factory=dict)

    @property
    def fallbacks(self) -> list[str]:
        return [c.provider_key for c in self.candidates if c.provider_key != self.selected]


def quality_prior(provider_key: str, field_name: str) -> float:
    table = FIELD_QUALITY_PRIORS.get(provider_key, {})
    return table.get(field_name, table.get("_default", 0.75))


def _recent_health(session: Session | None, provider_key: str) -> dict:
    if session is None:
        return {}
    row = session.execute(
        select(ProviderHealthWindow)
        .where(ProviderHealthWindow.provider_key == provider_key)
        .order_by(ProviderHealthWindow.window_start.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None or not row.request_count:
        return {}
    return {
        "error_rate": 1 - row.success_count / row.request_count,
        "p95_latency_ms": row.p95_latency_ms,
        "rate_limited": row.rate_limited_count,
    }


def _dynamic_performance(session: Session | None, tenant_id: str | None, provider_key: str,
                         field_name: str) -> dict:
    """Historical field-level performance: acceptance share of this provider's observations
    (selected / total) and fill behavior. Falls back to priors when history is thin."""
    if session is None or tenant_id is None:
        return {}
    total, selected, rejected = session.execute(
        select(
            func.count(FieldObservation.id),
            func.coalesce(func.sum(case((FieldObservation.is_selected, 1), else_=0)), 0),
            func.coalesce(func.sum(case((FieldObservation.is_rejected, 1), else_=0)), 0),
        ).where(
            FieldObservation.tenant_id == tenant_id,
            FieldObservation.provider_key == provider_key,
            FieldObservation.field_name == field_name,
        )
    ).one()
    if not total or total < 5:  # not enough history to trust
        return {}
    return {
        "observed_precision": float(selected) / float(total),
        "observed_rejection": float(rejected) / float(total),
        "history_n": int(total),
    }


def score_candidate(
    strategy: str,
    provider_key: str,
    adapter: ProviderAdapter,
    entity_type: str,
    field_name: str,
    *,
    session: Session | None = None,
    tenant_id: str | None = None,
    breaker_open: bool = False,
) -> Candidate:
    cost = adapter.field_cost(entity_type, field_name)
    quality = quality_prior(provider_key, field_name)
    health = _recent_health(session, provider_key)
    error_rate = health.get("error_rate", 0.0)
    p95 = health.get("p95_latency_ms")
    health_penalty = min(0.5, error_rate * 2)  # 25% errors halve the score
    if breaker_open:
        health_penalty = 1.0

    factors: dict = {
        "cost": cost, "quality_prior": quality, "error_rate": round(error_rate, 4),
        "p95_latency_ms": p95, "breaker_open": breaker_open, "strategy": strategy,
    }

    if strategy == "dynamic":
        perf = _dynamic_performance(session, tenant_id, provider_key, field_name)
        if perf:
            # Blend prior with observed precision, weighted by history volume (cap at 50 obs).
            n = min(perf["history_n"], 50)
            blend = (quality * (50 - n) + perf["observed_precision"] * n) / 50
            factors.update(perf, quality_blended=round(blend, 4))
            quality = blend

    if strategy == "cheapest_capable":
        score = (1.0 / max(cost, 0.01)) * (1 - health_penalty) * (1 if quality >= 0.6 else 0.2)
    elif strategy == "quality_first":
        score = quality * (1 - health_penalty) + (0.05 / max(cost, 0.05))
    else:  # balanced | dynamic share the same combining form once quality is blended
        latency_penalty = 0.1 if (p95 or 0) > 2000 else 0.0
        score = (quality / (max(cost, 0.01) ** 0.5)) * (1 - health_penalty) * (1 - latency_penalty)

    return Candidate(
        provider_key=provider_key,
        cost=cost,
        quality=round(quality, 4),
        health_penalty=round(health_penalty, 4),
        p95_latency_ms=p95,
        score=round(score, 4),
        factors=factors,
    )


def route_fields(
    entity_type: str,
    fields: list[str],
    registry: ProviderRegistry,
    policy: dict | None = None,
    *,
    session: Session | None = None,
    tenant_id: str | None = None,
    strategy_override: str | None = None,
    provider_allowlist: set[str] | None = None,
) -> list[FieldRoute]:
    policy = policy or DEFAULT_POLICY
    defaults = policy.get("defaults", {})
    routes: list[FieldRoute] = []

    for field_name in fields:
        key = f"{entity_type}.{field_name}"
        spec = policy.get("fields", {}).get(key, {})
        strategy = strategy_override or spec.get("strategy") or defaults.get("strategy", "balanced")
        preferred = spec.get("providers") or list(registry.all().keys())
        max_candidates = spec.get("max_candidates", defaults.get("max_candidates", 3))

        candidates: list[Candidate] = []
        rejected: list[dict] = []
        for pk in preferred:
            adapter = registry.get(pk)
            if adapter is None:
                rejected.append({"provider": pk, "reason": "not enabled"})
                continue
            if provider_allowlist is not None and pk not in provider_allowlist:
                rejected.append({"provider": pk, "reason": "excluded by budget degradation"})
                continue
            if not adapter.supports(entity_type, field_name):
                rejected.append({"provider": pk, "reason": f"does not support {key}"})
                continue
            breaker_open = not registry.breaker(pk).allow()
            cand = score_candidate(
                strategy, pk, adapter, entity_type, field_name,
                session=session, tenant_id=tenant_id, breaker_open=breaker_open,
            )
            if breaker_open:
                rejected.append({"provider": pk, "reason": "circuit breaker open", "factors": cand.factors})
                continue
            candidates.append(cand)

        candidates.sort(key=lambda c: c.score, reverse=True)
        candidates = candidates[:max_candidates]
        selected = candidates[0].provider_key if candidates else None
        routes.append(
            FieldRoute(
                field_name=field_name,
                strategy=strategy,
                candidates=candidates,
                selected=selected,
                rejected=rejected,
                expected_cost=candidates[0].cost if candidates else 0.0,
                factors={"policy_key": key if key in policy.get("fields", {}) else "defaults"},
            )
        )
    return routes


def group_by_provider(routes: list[FieldRoute]) -> dict[str, list[str]]:
    """Batch fields per selected provider → one provider call per provider."""
    grouped: dict[str, list[str]] = {}
    for r in routes:
        if r.selected:
            grouped.setdefault(r.selected, []).append(r.field_name)
    return grouped
