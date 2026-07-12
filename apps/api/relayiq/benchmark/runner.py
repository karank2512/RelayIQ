"""Strategy benchmark: naive vs cache vs filter+cache vs static routing vs full RelayIQ
vs dynamic routing — identical seeded synthetic data, real simulators, real reconciliation.

WHAT THIS MEASURES (be precise when citing results):
- Provider economics are SIMULATED (simulator personalities/costs), but the control-plane
  behavior — caching, filtering, routing, reconciliation, confidence gating — is the real
  RelayIQ code, and quality is scored against the synthetic world's known truth.
- "true usable" = the strategy delivered a lead its own rules consider usable AND the
  delivered job_title/seniority match ground truth — filling a CRM with wrong values does
  not count.

The submission stream includes duplicate submissions (like repeated webhook deliveries):
strategies without idempotency/caching pay for them again.
"""

import random
import time
from dataclasses import dataclass, field

from relayiq.canonical.normalize import (
    is_valid_domain,
    normalize_value,
    values_equivalent,
)
from relayiq.engines.confidence import FieldConfidenceInput, score_entity, score_field
from relayiq.engines.reconciliation import reconcile_field
from relayiq.enums import ReconciliationOutcome
from relayiq.models import FieldObservation
from relayiq.providers.base import EnrichmentCallResult
from relayiq.providers.simulators import SimulatedProvider, make_alpha, make_beta
from relayiq.seed.worldgen import generate_world, write_world
from relayiq.services.staleness import DEFAULTS, FALLBACK, freshness_factor

CONTACT_FIELDS = ["job_title", "seniority", "department", "linkedin_url"]
PROVIDER_PRIORS = {"alpha": 0.86, "beta": 0.9}
# Static field-level routing table (mirrors DEFAULT_POLICY): field -> [primary, fallback]
STATIC_ROUTES = {
    "job_title": ["beta", "alpha"],
    "seniority": ["beta", "alpha"],
    "department": ["beta", "alpha"],
    "linkedin_url": ["beta", "alpha"],
}
CROSS_CHECK_FIELDS = {"job_title"}  # full strategy buys a second opinion when primary is stale
MIN_CONFIDENCE = 0.6
FILTERED_COUNTRIES = {"United States", "Canada", "United Kingdom", "Germany"}


@dataclass
class StrategyResult:
    name: str
    description: str
    submissions: int = 0
    unique_records: int = 0
    provider_calls: int = 0
    cost_credits: float = 0.0
    fields_requested: int = 0
    fields_filled: int = 0
    fields_correct: int = 0
    claimed_usable: int = 0
    true_usable: int = 0
    review_records: int = 0
    filtered_records: int = 0
    wall_seconds: float = 0.0
    notes: str = ""
    extra: dict = field(default_factory=dict)

    def summary(self) -> dict:
        def per(n: int) -> float | None:
            return round(self.cost_credits / n, 3) if n else None

        return {
            "strategy": self.name,
            "description": self.description,
            "submissions": self.submissions,
            "unique_records": self.unique_records,
            "provider_calls": self.provider_calls,
            "cost_credits": round(self.cost_credits, 2),
            "fill_rate": round(self.fields_filled / self.fields_requested, 4)
            if self.fields_requested else None,
            "field_precision_vs_truth": round(self.fields_correct / self.fields_filled, 4)
            if self.fields_filled else None,
            "claimed_usable_leads": self.claimed_usable,
            "true_usable_leads": self.true_usable,
            "cost_per_claimed_usable": per(self.claimed_usable),
            "cost_per_true_usable": per(self.true_usable),
            "review_records": self.review_records,
            "filtered_records": self.filtered_records,
            "wall_seconds": round(self.wall_seconds, 2),
            "notes": self.notes,
            **self.extra,
        }


def _mk_providers(world_path: str, seed: int) -> dict[str, SimulatedProvider]:
    return {
        "alpha": make_alpha(world_path=world_path, seed=seed),
        "beta": make_beta(world_path=world_path, seed=seed),
    }


def _call(provider: SimulatedProvider, contact: dict, fields: list[str],
          result: StrategyResult) -> EnrichmentCallResult:
    email = contact["truth"].get("work_email") or contact["world_id"]
    r = provider.enrich("contact", {"work_email": email, "world_id": contact["world_id"]}, fields)
    result.provider_calls += 1
    result.cost_credits += r.cost_credits
    return r


def _is_prefiltered(contact: dict, company: dict) -> bool:
    """Pre-enrichment filter mirror: suppression, invalid domain, campaign country filter,
    low-value, missing identifiers."""
    if "suppressed" in contact["tags"] or "suppressed" in company["tags"]:
        return True
    if "invalid_domain" in company["tags"]:
        return True
    if contact["truth"].get("hq_country", company["truth"].get("hq_country")) not in FILTERED_COUNTRIES:
        return True
    if "low_value" in company["tags"]:
        return True
    if not contact["truth"].get("work_email"):
        return True
    return False


def _truth_correct(contact: dict, field_name: str, value) -> bool:
    truth = contact["truth"].get(field_name)
    if truth is None:
        return False
    return values_equivalent(field_name, str(value), str(truth))


def _lead_quality(contact: dict, company: dict, delivered: dict) -> tuple[bool, bool]:
    """(claimed_usable, true_usable) for a delivered field dict {field: value}.

    Applies the documented usable-lead definition (docs/benchmarks/metric-definitions.md):
    valid company domain, contact name, title-or-seniority present, AND no suppression /
    policy violation / campaign misfit. A strategy that enriches a suppressed or
    out-of-campaign record has spent money on a lead that can never be usable."""
    domain = company["truth"].get("root_domain") or ""
    claimed = bool(
        is_valid_domain(domain)
        and contact["truth"].get("full_name")
        and (delivered.get("job_title") or delivered.get("seniority"))
        and not _is_prefiltered(contact, company)
    )
    if not claimed:
        return False, False
    title_ok = "job_title" in delivered and _truth_correct(contact, "job_title", delivered["job_title"])
    seniority_ok = "seniority" in delivered and _truth_correct(contact, "seniority", delivered["seniority"])
    return True, (title_ok or seniority_ok)


def _score_delivery(contact: dict, company: dict, delivered: dict, result: StrategyResult,
                    *, usable_eligible: bool = True) -> None:
    """Fill/precision/usable metrics are computed over ELIGIBLE records only (records a
    well-configured campaign actually wants) so strategies are compared on the same
    denominator; spend on ineligible records still shows up in cost."""
    if _is_prefiltered(contact, company):
        return
    result.fields_requested += len(CONTACT_FIELDS)
    result.fields_filled += len(delivered)
    result.fields_correct += sum(
        1 for f, v in delivered.items() if _truth_correct(contact, f, v)
    )
    if usable_eligible:
        claimed, true = _lead_quality(contact, company, delivered)
        result.claimed_usable += int(claimed)
        result.true_usable += int(true)


def _obs_from_call(contact_id: str, r: EnrichmentCallResult, field_name: str) -> FieldObservation | None:
    fv = r.fields.get(field_name)
    if fv is None:
        return None
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    raw = str(fv.value)
    return FieldObservation(
        tenant_id="bench", entity_type="contact", entity_id=contact_id,
        field_name=field_name, raw_value=raw,
        normalized_value=normalize_value(field_name, raw),
        provider_key=r.provider_key,
        source_timestamp=now - timedelta(days=fv.source_age_days or 0),
        retrieved_at=now,
        provider_confidence=fv.provider_confidence,
        cost_credits=0,
        validation_results={},
    )


def _thresholds(field_name: str):
    return DEFAULTS.get(("contact", field_name), FALLBACK)


def _make_stream(world: dict, duplicate_submission_rate: float, seed: int) -> list[dict]:
    rng = random.Random(seed + 1)  # noqa: S311 — deterministic simulation, not crypto
    contacts = list(world["contacts"])
    stream = list(contacts)
    n_dupes = int(len(contacts) * duplicate_submission_rate)
    stream.extend(rng.choice(contacts) for _ in range(n_dupes))
    rng.shuffle(stream)
    return stream


def run_benchmark(
    seed: int = 42,
    n_companies: int = 200,
    duplicate_submission_rate: float = 0.15,
    world_path: str | None = None,
) -> dict:
    """Run all strategies over identical data. Returns {config, environment, results: [...]}."""
    import tempfile
    from pathlib import Path

    world = generate_world(seed=seed, n_companies=n_companies)
    if world_path is None:
        world_path = str(Path(tempfile.mkdtemp(prefix="riq-bench-")) / "world.json")
    write_world(world, world_path)
    companies = {c["world_id"]: c for c in world["companies"]}
    stream = _make_stream(world, duplicate_submission_rate, seed)
    results = []
    for fn in (_naive, _cache_only, _filter_cache, _static_routing, _relayiq_full, _dynamic):
        providers = _mk_providers(world_path, seed)  # fresh limiters/breakers per strategy
        t0 = time.monotonic()
        res = fn(stream, companies, providers)
        res.wall_seconds = time.monotonic() - t0
        res.submissions = len(stream)
        res.unique_records = len({c["world_id"] for c in stream})
        results.append(res)
    return {
        "config": {
            "seed": seed, "n_companies": n_companies,
            "contacts": len(world["contacts"]),
            "duplicate_submission_rate": duplicate_submission_rate,
            "requested_fields": CONTACT_FIELDS,
            "world_config": world["config"],
        },
        "measurement_basis": (
            "Simulated providers (deterministic personalities), real RelayIQ control-plane "
            "code, quality scored against known synthetic truth."
        ),
        "results": [r.summary() for r in results],
    }


# ── Strategies ──────────────────────────────────────────────────────────────

def _naive(stream, companies, providers) -> StrategyResult:
    """Every submission calls EVERY provider for every field; first non-null wins
    (provider order alpha→beta). No cache, no filters, no dedupe — duplicate webhook
    deliveries buy everything twice."""
    res = StrategyResult("naive", "all providers, all fields, every submission; no controls")
    for contact in stream:
        company = companies[contact["company_world_id"]]
        delivered: dict = {}
        for key in ("alpha", "beta"):
            r = _call(providers[key], contact, CONTACT_FIELDS, res)
            for f, fv in r.fields.items():
                delivered.setdefault(f, fv.value)
        _score_delivery(contact, company, delivered, res)
    return res


def _cache_only(stream, companies, providers) -> StrategyResult:
    """Naive + an idempotency/cache layer: a repeated submission reuses the first answer."""
    res = StrategyResult("cache_only", "naive + cache: duplicates served for free")
    cache: dict[str, dict] = {}
    for contact in stream:
        company = companies[contact["company_world_id"]]
        if contact["world_id"] in cache:
            _score_delivery(contact, company, cache[contact["world_id"]], res)
            continue
        delivered: dict = {}
        for key in ("alpha", "beta"):
            r = _call(providers[key], contact, CONTACT_FIELDS, res)
            for f, fv in r.fields.items():
                delivered.setdefault(f, fv.value)
        cache[contact["world_id"]] = delivered
        _score_delivery(contact, company, delivered, res)
    return res


def _filter_cache(stream, companies, providers) -> StrategyResult:
    """Pre-enrichment filters (suppression, invalid domains, campaign fit, low value)
    + cache. Rejected records spend zero."""
    res = StrategyResult("filter_cache", "pre-filters + cache; still calls both providers")
    cache: dict[str, dict] = {}
    for contact in stream:
        company = companies[contact["company_world_id"]]
        if _is_prefiltered(contact, company):
            res.filtered_records += 1
            continue
        if contact["world_id"] in cache:
            _score_delivery(contact, company, cache[contact["world_id"]], res)
            continue
        delivered: dict = {}
        for key in ("alpha", "beta"):
            r = _call(providers[key], contact, CONTACT_FIELDS, res)
            for f, fv in r.fields.items():
                delivered.setdefault(f, fv.value)
        cache[contact["world_id"]] = delivered
        _score_delivery(contact, company, delivered, res)
    return res


def _route_and_reconcile(stream, companies, providers, res, *, routes_for, cross_check: bool):
    """Shared engine for static/full/dynamic: field-level routing + fallback +
    reconciliation + confidence gating."""
    cache: dict[str, dict] = {}
    for contact in stream:
        company = companies[contact["company_world_id"]]
        if _is_prefiltered(contact, company):
            res.filtered_records += 1
            continue
        if contact["world_id"] in cache:
            _score_delivery(contact, company, cache[contact["world_id"]], res)
            continue

        # Group fields by primary provider → one call per provider
        plan: dict[str, list[str]] = {}
        for f in CONTACT_FIELDS:
            plan.setdefault(routes_for(f)[0], []).append(f)
        calls: dict[str, EnrichmentCallResult] = {}
        for pk, fields in plan.items():
            calls[pk] = _call(providers[pk], contact, fields, res)

        obs_by_field: dict[str, list[FieldObservation]] = {}
        for pk, fields in plan.items():
            for f in fields:
                obs = _obs_from_call(contact["world_id"], calls[pk], f)
                if obs is not None:
                    obs_by_field.setdefault(f, []).append(obs)
                else:
                    # fallback provider for missing field
                    for fb in routes_for(f)[1:]:
                        r2 = _call(providers[fb], contact, [f], res)
                        obs2 = _obs_from_call(contact["world_id"], r2, f)
                        if obs2 is not None:
                            obs_by_field.setdefault(f, []).append(obs2)
                            break

        if cross_check:
            # Buy a second opinion when the primary answer is stale on high-risk fields.
            for f in CROSS_CHECK_FIELDS & set(obs_by_field):
                primary = obs_by_field[f][0]
                age = (primary.retrieved_at - primary.source_timestamp).days
                if age > _thresholds(f).fresh_days and len(obs_by_field[f]) == 1:
                    others = [p for p in routes_for(f) if p != primary.provider_key]
                    if others:
                        r2 = _call(providers[others[0]], contact, [f], res)
                        obs2 = _obs_from_call(contact["world_id"], r2, f)
                        if obs2 is not None:
                            obs_by_field[f].append(obs2)

        delivered: dict = {}
        field_scores: dict[str, float] = {}
        needs_review = False
        for f, obs_list in obs_by_field.items():
            recon = reconcile_field("contact", f, obs_list,
                                    provider_priors=PROVIDER_PRIORS, thresholds=_thresholds(f))
            if recon.outcome in (ReconciliationOutcome.AUTO_ACCEPT,
                                 ReconciliationOutcome.ACCEPT_WITH_WARNING) and recon.chosen:
                chosen = recon.chosen
                age = (chosen.retrieved_at - chosen.source_timestamp).days
                conf = score_field(FieldConfidenceInput(
                    provider_reliability_prior=PROVIDER_PRIORS.get(chosen.provider_key, 0.8),
                    field_quality_prior=0.85,
                    freshness_factor=freshness_factor(age, _thresholds(f)),
                    agreement=recon.agreement,
                    format_valid=True,
                    provider_native_confidence=chosen.provider_confidence,
                    conflict_severity=recon.conflict_severity,
                )).score
                field_scores[f] = conf
                delivered[f] = chosen.normalized_value or chosen.raw_value
            elif recon.outcome == ReconciliationOutcome.REQUIRE_REVIEW:
                needs_review = True

        entity_conf = score_entity(field_scores, requested_fields=CONTACT_FIELDS).score if field_scores else 0.0  # noqa: E501
        if needs_review or entity_conf < MIN_CONFIDENCE:
            res.review_records += 1
            cache[contact["world_id"]] = delivered  # cache what we have; lead not counted usable
            _score_delivery(contact, company, delivered, res, usable_eligible=False)
            continue
        cache[contact["world_id"]] = delivered
        _score_delivery(contact, company, delivered, res)
    return res


def _static_routing(stream, companies, providers) -> StrategyResult:
    res = StrategyResult(
        "static_routing",
        "filters + cache + static field-level routing (one provider per field + fallback) + reconciliation",
    )
    return _route_and_reconcile(stream, companies, providers, res,
                                routes_for=lambda f: STATIC_ROUTES[f], cross_check=False)


def _relayiq_full(stream, companies, providers) -> StrategyResult:
    res = StrategyResult(
        "relayiq_full",
        "static routing + staleness-triggered cross-check + confidence gating (review queue)",
    )
    res.notes = ("review_records are excluded from usable leads (conservative: human review "
                 "not simulated)")
    return _route_and_reconcile(stream, companies, providers, res,
                                routes_for=lambda f: STATIC_ROUTES[f], cross_check=True)


def _dynamic(stream, companies, providers) -> StrategyResult:
    """Warmup 15% of unique records to estimate per-field provider precision, then route
    each field to argmax(observed_precision / cost^0.5)."""
    res = StrategyResult(
        "dynamic_routing",
        "learned field-level provider choice from warmup precision/cost, then full pipeline",
    )
    rng = random.Random(99)  # noqa: S311 — deterministic simulation, not crypto
    unique = list({c["world_id"]: c for c in stream}.values())
    warmup = rng.sample(unique, max(5, int(len(unique) * 0.15)))
    stats: dict[tuple[str, str], list[int]] = {}
    for contact in warmup:
        for pk in ("alpha", "beta"):
            r = _call(providers[pk], contact, CONTACT_FIELDS, res)
            res.fields_requested += 0  # warmup spend is counted in cost, not fill metrics
            for f in CONTACT_FIELDS:
                fv = r.fields.get(f)
                if fv is not None:
                    ok = _truth_correct(contact, f, fv.value)  # proxy for reviewed precision
                    stats.setdefault((pk, f), []).append(int(ok))
    res.extra["warmup_records"] = len(warmup)

    def routes_for(f: str) -> list[str]:
        def score(pk: str) -> float:
            history = stats.get((pk, f), [])
            precision = sum(history) / len(history) if history else 0.75
            cost = providers[pk].field_cost("contact", f)
            return precision / (cost ** 0.5)

        return sorted(("alpha", "beta"), key=score, reverse=True)

    res.extra["learned_routes"] = {f: routes_for(f)[0] for f in CONTACT_FIELDS}
    return _route_and_reconcile(stream, companies, providers, res,
                                routes_for=routes_for, cross_check=True)
