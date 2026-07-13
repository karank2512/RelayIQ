"""Unit tests: staleness, confidence, reconciliation, routing — the decision engines."""

from datetime import UTC, datetime, timedelta

import pytest

from relayiq.engines import confidence, routing
from relayiq.engines.reconciliation import reconcile_field
from relayiq.enums import ReconciliationOutcome, StalenessState
from relayiq.models import FieldObservation, StalenessPolicy
from relayiq.providers.registry import ProviderRegistry
from relayiq.providers.simulators import make_alpha, make_beta
from relayiq.services import staleness

NOW = datetime.now(UTC)
PRIORS = {"alpha": 0.86, "beta": 0.9}
T = staleness.Thresholds(30, 60, 90)


def obs(provider: str, value: str, *, field: str = "job_title", age_days: int = 5,
        native: float | None = 0.9, normalized: str | None = None) -> FieldObservation:
    return FieldObservation(
        tenant_id="t", entity_type="contact", entity_id="e1", field_name=field,
        raw_value=value, normalized_value=normalized if normalized is not None else value.lower(),
        provider_key=provider, provider_confidence=native,
        source_timestamp=NOW - timedelta(days=age_days), retrieved_at=NOW,
        validation_results={},
    )


# ── Staleness ───────────────────────────────────────────────────────────────

class TestStaleness:
    @pytest.mark.parametrize("age,expected", [
        (0, StalenessState.FRESH), (30, StalenessState.FRESH),
        (31, StalenessState.AGING), (60, StalenessState.AGING),
        (61, StalenessState.STALE), (90, StalenessState.STALE),
        (91, StalenessState.EXPIRED), (10_000, StalenessState.EXPIRED),
    ])
    def test_classify_boundaries(self, age, expected):
        assert staleness.classify_age(age, T) == expected

    def test_unknown_age(self):
        assert staleness.classify_age(None, T) == StalenessState.UNKNOWN

    def test_tenant_override_precedence(self, session, tenant):
        session.add(StalenessPolicy(tenant_id=None, entity_type="contact",
                                    field_name="job_title", fresh_days=10, aging_days=20, stale_days=30))
        session.add(StalenessPolicy(tenant_id=tenant.id, entity_type="contact",
                                    field_name="job_title", fresh_days=5, aging_days=6, stale_days=7))
        session.commit()
        t = staleness.get_thresholds(session, tenant.id, "contact", "job_title")
        assert (t.fresh_days, t.stale_days) == (5, 7)  # tenant row wins
        t_global = staleness.get_thresholds(session, "other-tenant", "contact", "job_title")
        assert t_global.fresh_days == 10  # global row when no tenant row
        t_builtin = staleness.get_thresholds(session, tenant.id, "account", "industry")
        assert t_builtin.fresh_days == 180  # builtin default

    def test_freshness_decay(self):
        assert staleness.freshness_factor(0, T) == pytest.approx(1.0)
        assert staleness.freshness_factor(90, T) == pytest.approx(0.5, abs=0.01)
        assert staleness.freshness_factor(400, T) < 0.1
        assert staleness.freshness_factor(None, T) == 0.5  # unknown → neutral

    def test_reusability(self):
        assert staleness.is_reusable(StalenessState.FRESH)
        assert staleness.is_reusable(StalenessState.AGING)
        assert not staleness.is_reusable(StalenessState.STALE)
        assert not staleness.is_reusable(StalenessState.EXPIRED)
        assert not staleness.is_reusable(StalenessState.UNKNOWN)


# ── Confidence ──────────────────────────────────────────────────────────────

class TestConfidence:
    def test_bounded_and_explainable(self):
        r = confidence.score_field(confidence.FieldConfidenceInput(
            provider_reliability_prior=0.9, field_quality_prior=0.9, freshness_factor=1.0,
            agreement=1.0, format_valid=True, provider_native_confidence=0.95,
        ))
        assert 0.0 <= r.score <= 1.0
        assert r.score > 0.85
        assert "weighted_base" in r.components and "penalty" in r.components

    def test_weight_redistribution_when_components_missing(self):
        full = confidence.score_field(confidence.FieldConfidenceInput(
            freshness_factor=0.8, agreement=0.8, format_valid=True))
        sparse = confidence.score_field(confidence.FieldConfidenceInput(
            freshness_factor=0.8, agreement=None, format_valid=None))
        # score remains defined and in-range with missing components
        assert 0.0 <= sparse.score <= 1.0
        assert full.score != sparse.score

    def test_conflict_penalty_lowers_score(self):
        base = confidence.FieldConfidenceInput(freshness_factor=0.9, format_valid=True)
        clean = confidence.score_field(base).score
        conflicted = confidence.score_field(
            confidence.FieldConfidenceInput(freshness_factor=0.9, format_valid=True,
                                            conflict_severity=1.0)).score
        assert clean - conflicted == pytest.approx(0.25, abs=0.01)

    def test_entity_required_field_weighting_and_fill(self):
        high_required = confidence.score_entity(
            {"job_title": 0.9, "department": 0.4}, required_fields=["job_title"],
            requested_fields=["job_title", "department"])
        low_required = confidence.score_entity(
            {"job_title": 0.4, "department": 0.9}, required_fields=["job_title"],
            requested_fields=["job_title", "department"])
        assert high_required.score > low_required.score
        partial = confidence.score_entity(
            {"job_title": 0.9}, requested_fields=["job_title", "a", "b", "c"])
        complete = confidence.score_entity({"job_title": 0.9}, requested_fields=["job_title"])
        assert complete.score > partial.score  # missingness penalized

    def test_identity_match_certainty_scales(self):
        sure = confidence.score_entity({"f": 0.9}, identity_match_certainty=1.0)
        unsure = confidence.score_entity({"f": 0.9}, identity_match_certainty=0.5)
        assert unsure.score == pytest.approx(sure.score * 0.5, abs=0.01)

    def test_sync_level_floor_and_conflict_penalty(self):
        r = confidence.score_sync(0.9, {"a": 0.7, "b": 0.95}, unresolved_conflicts=0)
        assert r.score == pytest.approx(0.7, abs=0.001)  # min floor
        r2 = confidence.score_sync(0.9, {"a": 0.7}, unresolved_conflicts=2)
        assert r2.score == pytest.approx(0.7 - 0.3, abs=0.001)


# ── Reconciliation ──────────────────────────────────────────────────────────

class TestReconciliation:
    def test_all_agree_auto_accepts(self):
        r = reconcile_field("contact", "job_title",
                            [obs("alpha", "VP Sales", normalized="vice president sales"),
                             obs("beta", "Vice President of Sales",
                                 normalized="vice president of sales")],
                            provider_priors=PRIORS, thresholds=T)
        assert r.outcome == ReconciliationOutcome.AUTO_ACCEPT
        assert r.conflict_severity == 0.0
        assert r.agreement == 1.0

    def test_normalized_company_name_agreement(self):
        r = reconcile_field("account", "name",
                            [obs("alpha", "Acme Inc.", field="name", normalized="acme"),
                             obs("beta", "Acme Corporation", field="name", normalized="acme")],
                            provider_priors=PRIORS, thresholds=T)
        assert r.outcome == ReconciliationOutcome.AUTO_ACCEPT

    def test_domain_conflict_is_high_severity_review(self):
        r = reconcile_field("account", "root_domain",
                            [obs("alpha", "acme.com", field="root_domain", normalized="acme.com"),
                             obs("beta", "getacme.com", field="root_domain", normalized="getacme.com")],
                            provider_priors=PRIORS, thresholds=T)
        assert r.outcome == ReconciliationOutcome.REQUIRE_REVIEW
        assert r.conflict_severity == pytest.approx(0.9)
        assert "acme.com" in r.reasoning and "getacme.com" in r.reasoning

    def test_adjacent_employee_ranges_mild(self):
        r = reconcile_field("account", "employee_range",
                            [obs("alpha", "51-200", field="employee_range", normalized="51-200"),
                             obs("beta", "201-500", field="employee_range", normalized="201-500")],
                            provider_priors=PRIORS, thresholds=T)
        assert r.conflict_severity == pytest.approx(0.3)

    def test_all_invalid_rejects_all(self):
        bad1 = obs("alpha", "not a domain!!", field="root_domain", normalized="not a domain!!")
        bad2 = obs("beta", "also bad!!", field="root_domain", normalized="also bad!!")
        r = reconcile_field("account", "root_domain", [bad1, bad2],
                            provider_priors=PRIORS, thresholds=T)
        assert r.outcome == ReconciliationOutcome.REJECT_ALL

    def test_fresh_crm_disagreement_retains_crm(self):
        r = reconcile_field("contact", "job_title",
                            [obs("alpha", "CTO", normalized="cto"),
                             obs("beta", "VP Engineering", normalized="vice president engineering")],
                            provider_priors=PRIORS, thresholds=T,
                            crm_value="Chief Product Officer", crm_state=StalenessState.FRESH)
        assert r.outcome == ReconciliationOutcome.RETAIN_CRM
        assert "Retaining the CRM value" in r.reasoning

    def test_no_observations_unresolved(self):
        r = reconcile_field("contact", "job_title", [], provider_priors=PRIORS, thresholds=T)
        assert r.outcome == ReconciliationOutcome.UNRESOLVED

    def test_rejected_observations_ignored(self):
        rejected = obs("alpha", "Old Title")
        rejected.is_rejected = True
        r = reconcile_field("contact", "job_title", [rejected, obs("beta", "CEO", normalized="ceo")],
                            provider_priors=PRIORS, thresholds=T)
        assert r.outcome == ReconciliationOutcome.AUTO_ACCEPT
        assert r.chosen.provider_key == "beta"

    def test_reasoning_is_human_readable(self):
        r = reconcile_field("contact", "job_title",
                            [obs("alpha", "VP Sales", normalized="vice president sales", age_days=200),
                             obs("beta", "Director of Sales", normalized="director of sales", age_days=2)],
                            provider_priors=PRIORS, thresholds=T)
        assert "alpha" in r.reasoning and "beta" in r.reasoning
        assert r.factors["groups"][0]["weight"] > 0


# ── Routing ─────────────────────────────────────────────────────────────────

def _registry(world_file: str) -> ProviderRegistry:
    reg = ProviderRegistry()
    reg._adapters = {"alpha": make_alpha(world_path=world_file, seed=7),
                     "beta": make_beta(world_path=world_file, seed=7)}
    return reg


@pytest.fixture()
def world_file(tmp_path):
    from relayiq.seed.worldgen import generate_world, write_world

    p = tmp_path / "world.json"
    write_world(generate_world(seed=7, n_companies=10), str(p))
    return str(p)


class TestRouting:
    def test_default_policy_field_level_split(self, world_file):
        reg = _registry(world_file)
        routes = routing.route_fields("contact", ["job_title"], reg)
        assert routes[0].selected == "beta"  # quality_first → fresh-titles provider
        routes = routing.route_fields("account", ["root_domain"], reg)
        assert routes[0].selected == "alpha"  # cheapest_capable → firmographics provider

    def test_open_breaker_rejects_provider(self, world_file):
        reg = _registry(world_file)
        breaker = reg.breaker("beta")
        for _ in range(breaker.threshold):
            breaker.record_failure()
        routes = routing.route_fields("contact", ["job_title"], reg)
        assert routes[0].selected == "alpha"
        assert any(r["provider"] == "beta" and "circuit" in r["reason"]
                   for r in routes[0].rejected)

    def test_allowlist_exclusion(self, world_file):
        reg = _registry(world_file)
        routes = routing.route_fields("contact", ["job_title"], reg,
                                      provider_allowlist={"alpha"})
        assert routes[0].selected == "alpha"

    def test_decision_factors_recorded(self, world_file):
        reg = _registry(world_file)
        route = routing.route_fields("contact", ["job_title"], reg)[0]
        assert all("cost" in c.factors and "quality_prior" in c.factors for c in route.candidates)
        assert route.expected_cost > 0

    def test_group_by_provider_batches(self, world_file):
        reg = _registry(world_file)
        routes = routing.route_fields(
            "contact", ["job_title", "seniority", "work_email"], reg)
        grouped = routing.group_by_provider(routes)
        assert sum(len(v) for v in grouped.values()) == 3
        assert set(grouped) <= {"alpha", "beta"}
