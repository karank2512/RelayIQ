"""Unit tests: budget concurrency-safety, idempotency, cache, ledger, decision engine,
CRM gate, simulators."""

import time
from datetime import UTC, datetime, timedelta

import pytest

from relayiq.enums import (
    CacheStatus,
    GateOutcome,
    PreDecision,
    ProviderOutcome,
    StalenessState,
)
from relayiq.models import Budget, Campaign, CanonicalFieldValue, EnrichmentJob, Suppression
from relayiq.services import budget as budget_service
from relayiq.services import idempotency, ledger
from relayiq.services.cache import FieldCache
from relayiq.services.crm_gate import FieldGateInput, gate_field
from relayiq.services.staleness import Thresholds  # noqa: F401 — fixture typing

# ── Budget ──────────────────────────────────────────────────────────────────

def make_budget(session, tenant, *, limit=10, kind="hard", warning=0.8, per_record=None) -> Budget:
    b = Budget(tenant_id=tenant.id, name="test", kind=kind, period="lifetime",
               limit_credits=limit, warning_threshold=warning, per_record_max=per_record)
    session.add(b)
    session.commit()
    return b


class TestBudget:
    def test_hard_budget_blocks_at_limit(self, session, tenant):
        b = make_budget(session, tenant, limit=10)
        assert budget_service.reserve(session, b, 6).allowed
        assert not budget_service.reserve(session, b, 6).allowed  # 12 > 10
        assert budget_service.reserve(session, b, 4).allowed      # exactly 10

    def test_sequential_reserves_never_exceed_limit(self, session, tenant):
        b = make_budget(session, tenant, limit=10)
        granted = sum(1 for _ in range(8) if budget_service.reserve(session, b, 3).allowed)
        session.refresh(b)
        assert granted == 3  # 3x3=9 fits, 4th would be 12
        assert float(b.reserved_credits) <= 10

    def test_soft_budget_warns_but_allows(self, session, tenant):
        b = make_budget(session, tenant, limit=10, kind="soft", warning=0.5)
        state = budget_service.reserve(session, b, 20)
        assert state.allowed and state.warning
        assert state.degradation_mode is not None

    def test_per_record_max(self, session, tenant):
        b = make_budget(session, tenant, limit=100, per_record=2)
        state = budget_service.reserve(session, b, 5)
        assert not state.allowed and "per-record" in state.reason

    def test_commit_spend_releases_remainder(self, session, tenant):
        b = make_budget(session, tenant, limit=10)
        budget_service.reserve(session, b, 6)
        budget_service.commit_spend(session, b, reserved=6, actual=2.5)
        session.refresh(b)
        assert float(b.spent_credits) == pytest.approx(2.5)
        assert float(b.reserved_credits) == pytest.approx(0)
        assert budget_service.reserve(session, b, 7).allowed  # 2.5 + 7 <= 10

    def test_release_frees_hold(self, session, tenant):
        b = make_budget(session, tenant, limit=10)
        budget_service.reserve(session, b, 10)
        budget_service.release(session, b, 10)
        assert budget_service.reserve(session, b, 10).allowed


# ── Idempotency ─────────────────────────────────────────────────────────────

class TestIdempotency:
    def test_claim_then_in_progress(self, session, tenant):
        first = idempotency.claim(session, tenant.id, "enrichment", "k1", "h1")
        assert first.outcome == idempotency.ClaimOutcome.NEW
        dup = idempotency.claim(session, tenant.id, "enrichment", "k1", "h1")
        assert dup.outcome == idempotency.ClaimOutcome.IN_PROGRESS

    def test_completed_replays_snapshot(self, session, tenant):
        c = idempotency.claim(session, tenant.id, "enrichment", "k2", "h1")
        idempotency.complete(session, c.record, {"job_id": "j-123", "cost": 0})
        replay = idempotency.claim(session, tenant.id, "enrichment", "k2", "h1")
        assert replay.outcome == idempotency.ClaimOutcome.COMPLETED
        assert replay.response_snapshot == {"job_id": "j-123", "cost": 0}

    def test_same_key_different_payload_mismatch(self, session, tenant):
        c = idempotency.claim(session, tenant.id, "enrichment", "k3", "hash-a")
        idempotency.complete(session, c.record, {})
        r = idempotency.claim(session, tenant.id, "enrichment", "k3", "hash-B")
        assert r.outcome == idempotency.ClaimOutcome.MISMATCH

    def test_failed_can_be_retried(self, session, tenant):
        c = idempotency.claim(session, tenant.id, "enrichment", "k4", "h")
        idempotency.fail(session, c.record)
        retry = idempotency.claim(session, tenant.id, "enrichment", "k4", "h")
        assert retry.outcome == idempotency.ClaimOutcome.NEW

    def test_expired_key_reclaimed(self, session, tenant):
        c = idempotency.claim(session, tenant.id, "enrichment", "k5", "h")
        idempotency.complete(session, c.record, {"old": True})
        c.record.expires_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()
        again = idempotency.claim(session, tenant.id, "enrichment", "k5", "h2")
        assert again.outcome == idempotency.ClaimOutcome.NEW

    def test_scopes_are_isolated(self, session, tenant):
        idempotency.claim(session, tenant.id, "enrichment", "k6", "h")
        other_scope = idempotency.claim(session, tenant.id, "webhook", "k6", "h")
        assert other_scope.outcome == idempotency.ClaimOutcome.NEW


# ── Cache ───────────────────────────────────────────────────────────────────

class TestCache:
    def make(self, redis_client) -> FieldCache:
        return FieldCache(client=redis_client)

    def test_roundtrip_and_negative(self, redis_client):
        c = self.make(redis_client)
        assert c.get_field("t1", "contact", "a@b.test", "job_title").status == CacheStatus.MISS
        c.set_field("t1", "contact", "a@b.test", "job_title", value="CEO",
                    normalized_value="ceo", provider_key="beta", confidence=0.9,
                    observation_id="o1", cost_credits=2.0)
        hit = c.get_field("t1", "contact", "a@b.test", "job_title")
        assert hit.status == CacheStatus.HIT and hit.value == "CEO"
        assert hit.avoided_cost_credits == 2.0
        c.set_negative("t1", "contact", "a@b.test", "linkedin_url")
        assert c.get_field("t1", "contact", "a@b.test", "linkedin_url").status == CacheStatus.NEGATIVE_HIT

    def test_soft_ttl_stale_hit(self, redis_client, monkeypatch):
        c = self.make(redis_client)
        c.set_field("t1", "contact", "a@b.test", "job_title", value="CEO",
                    normalized_value="ceo", provider_key="beta", confidence=0.9,
                    observation_id="o1", soft_ttl_seconds=10, ttl_seconds=100_000)
        real_now = time.time()
        # 1h forward: past the 10s soft TTL, inside the hard TTL (fakeredis expiry
        # also follows the patched clock, so the hard TTL must exceed the offset).
        monkeypatch.setattr(time, "time", lambda: real_now + 3600)
        assert c.get_field("t1", "contact", "a@b.test", "job_title").status == CacheStatus.STALE_HIT

    def test_tenant_isolation(self, redis_client):
        c = self.make(redis_client)
        c.set_field("t1", "contact", "a@b.test", "job_title", value="CEO",
                    normalized_value="ceo", provider_key="beta", confidence=0.9, observation_id="o")
        assert c.get_field("t2", "contact", "a@b.test", "job_title").status == CacheStatus.MISS

    def test_invalidation(self, redis_client):
        c = self.make(redis_client)
        for f in ("job_title", "seniority"):
            c.set_field("t1", "contact", "a@b.test", f, value="x", normalized_value="x",
                        provider_key="beta", confidence=0.5, observation_id="o")
        removed = c.invalidate_entity("t1", "contact", "a@b.test")
        assert removed == 2
        assert c.get_field("t1", "contact", "a@b.test", "job_title").status == CacheStatus.MISS

    def test_stampede_lock(self, redis_client):
        c = self.make(redis_client)
        token = c.acquire_refresh_lock("t1", "contact", "a@b.test", "job_title")
        assert token is not None
        assert c.acquire_refresh_lock("t1", "contact", "a@b.test", "job_title") is None
        c.release_refresh_lock("t1", "contact", "a@b.test", "job_title", "wrong-token")
        assert c.acquire_refresh_lock("t1", "contact", "a@b.test", "job_title") is None
        c.release_refresh_lock("t1", "contact", "a@b.test", "job_title", token)
        assert c.acquire_refresh_lock("t1", "contact", "a@b.test", "job_title") is not None


# ── Ledger ──────────────────────────────────────────────────────────────────

class TestLedger:
    def test_summary_aggregations(self, session, tenant):
        ledger.record_entry(session, tenant_id=tenant.id, operation="enrich_field",
                            provider_key="alpha", actual_cost=2.0)
        ledger.record_entry(session, tenant_id=tenant.id, operation="enrich_field",
                            cache_status="hit", avoided_cost=1.5)
        ledger.record_entry(session, tenant_id=tenant.id, operation="enrich_field",
                            provider_key="beta", actual_cost=3.0, spent_on_stale=True)
        session.commit()
        s = ledger.cost_summary(session, tenant.id)
        assert s["total_spend_credits"] == pytest.approx(5.0)
        assert s["redundant_cost_avoided_credits"] == pytest.approx(1.5)
        assert s["spend_on_stale_credits"] == pytest.approx(3.0)
        assert s["ledger_entries"] == 3

    def test_cost_per_denominators(self, session, tenant):
        for i, (status, summary, cost) in enumerate([
            ("completed", {"accepted": True, "usable_lead": True,
                           "all_requested_fields_filled": True}, 4.0),
            ("completed", {"accepted": True, "usable_lead": False}, 2.0),
            ("skipped", {}, 0.0),
        ]):
            session.add(EnrichmentJob(
                tenant_id=tenant.id, entity_type="contact", entity_id=f"e{i}",
                requested_fields=["job_title"], status=status, result_summary=summary,
                actual_cost_credits=cost,
            ))
        session.commit()
        m = ledger.cost_per(session, tenant.id)
        assert m["attempted_records"] == 3
        assert m["accepted_records"] == 2
        assert m["usable_leads"] == 1
        assert m["cost_per_usable_lead"] == pytest.approx(6.0)
        assert m["cost_per_accepted_record"] == pytest.approx(3.0)


# ── Decision engine ─────────────────────────────────────────────────────────

def make_decision_input(tenant, **overrides):
    from relayiq.engines.decision import DecisionInput
    from relayiq.services.budget import BudgetState

    defaults = dict(
        tenant_id=tenant.id, entity_type="contact", entity_id="e-1",
        requested_fields=["job_title"],
        identifiers={"work_email": "a@corp.test", "full_name": "A B", "root_domain": "corp.test"},
        campaign=None, budget_state=BudgetState(None, allowed=True),
        providers_available=True, estimated_min_cost=1.0,
    )
    defaults.update(overrides)
    return DecisionInput(**defaults)


class TestDecisionEngine:
    def test_suppressed_domain_policy_block(self, session, tenant):
        from relayiq.engines.decision import decide

        session.add(Suppression(tenant_id=tenant.id, kind="domain", value="corp.test"))
        session.commit()
        out = decide(session, make_decision_input(tenant))
        assert out.decision == PreDecision.POLICY_BLOCK

    def test_missing_identifiers_rejected(self, session, tenant):
        from relayiq.engines.decision import decide

        out = decide(session, make_decision_input(tenant, identifiers={}))
        assert out.decision == PreDecision.REJECT

    def test_bad_email_rejected(self, session, tenant):
        from relayiq.engines.decision import decide

        out = decide(session, make_decision_input(
            tenant, identifiers={"work_email": "not-an-email", "full_name": "A", "root_domain": "c.test"}))
        assert out.decision == PreDecision.REJECT

    def test_campaign_country_filter_skips(self, session, tenant):
        from relayiq.engines.decision import decide

        campaign = Campaign(tenant_id=tenant.id, name="c",
                            filters={"allowed_countries": ["United States"]})
        session.add(campaign)
        session.commit()
        out = decide(session, make_decision_input(
            tenant, campaign=campaign,
            identifiers={"work_email": "a@corp.test", "full_name": "A B",
                         "root_domain": "corp.test", "country": "France"}))
        assert out.decision == PreDecision.SKIP
        assert any("allowlist" in r for r in out.reasons)

    def test_fresh_canonical_fields_use_cache(self, session, tenant):
        from relayiq.engines.decision import decide

        session.add(CanonicalFieldValue(
            tenant_id=tenant.id, entity_type="contact", entity_id="e-1",
            field_name="job_title", value="CEO", normalized_value="ceo",
            confidence=0.9, staleness_state="fresh", last_verified_at=datetime.now(UTC),
        ))
        session.commit()
        out = decide(session, make_decision_input(tenant))
        assert out.decision == PreDecision.USE_CACHE
        assert out.fields_from_cache["job_title"]["value"] == "CEO"

    def test_budget_block(self, session, tenant):
        from relayiq.engines.decision import decide
        from relayiq.services.budget import BudgetState

        out = decide(session, make_decision_input(
            tenant, budget_state=BudgetState(None, allowed=False, reason="hard budget exceeded")))
        assert out.decision == PreDecision.BUDGET_BLOCK

    def test_inflight_duplicate_skips_but_not_self(self, session, tenant):
        from relayiq.engines.decision import decide

        job = EnrichmentJob(tenant_id=tenant.id, entity_type="contact", entity_id="e-1",
                            requested_fields=["job_title"], status="running")
        session.add(job)
        session.commit()
        assert decide(session, make_decision_input(tenant)).decision == PreDecision.SKIP
        assert decide(session, make_decision_input(
            tenant, current_job_id=job.id)).decision == PreDecision.ENRICH


# ── CRM gate ────────────────────────────────────────────────────────────────

def gate(session, tenant, **overrides):
    defaults = dict(
        field_name="job_title", new_value="ceo", confidence=0.9,
        has_unresolved_conflict=False, reconciliation_outcome=None,
        staleness_state=StalenessState.FRESH,
    )
    defaults.update(overrides)
    return gate_field(session, tenant.id, "contact", FieldGateInput(**defaults))


class TestCrmGate:
    def test_writes_when_clean(self, session, tenant):
        d = gate(session, tenant)
        assert d.outcome == GateOutcome.WRITE and d.reasons

    def test_low_confidence_requires_approval(self, session, tenant):
        d = gate(session, tenant, confidence=0.3)
        assert d.outcome == GateOutcome.REQUIRE_APPROVAL

    def test_unresolved_conflict_requires_approval(self, session, tenant):
        d = gate(session, tenant, has_unresolved_conflict=True)
        assert d.outcome == GateOutcome.REQUIRE_APPROVAL

    def test_reviewer_rejection_blocks(self, session, tenant):
        d = gate(session, tenant, reviewer_decision="rejected")
        assert d.outcome == GateOutcome.NO_WRITE

    def test_reviewer_acceptance_overrides_threshold(self, session, tenant):
        d = gate(session, tenant, confidence=0.2, reviewer_decision="accepted")
        assert d.outcome == GateOutcome.WRITE

    def test_equivalent_crm_value_no_write(self, session, tenant):
        d = gate(session, tenant, crm_value="CEO")
        assert d.outcome == GateOutcome.NO_WRITE

    def test_fresh_crm_value_preserved_at_modest_confidence(self, session, tenant):
        d = gate(session, tenant, confidence=0.7, crm_value="CTO",
                 crm_value_updated_at=datetime.now(UTC))
        assert d.outcome == GateOutcome.PRESERVE_CRM

    def test_fresh_crm_plus_high_confidence_secondary(self, session, tenant):
        d = gate(session, tenant, confidence=0.95, crm_value="CTO",
                 crm_value_updated_at=datetime.now(UTC))
        assert d.outcome == GateOutcome.SECONDARY_PROPERTY

    def test_stale_canonical_marks_refresh(self, session, tenant):
        d = gate(session, tenant, staleness_state=StalenessState.EXPIRED)
        assert d.outcome == GateOutcome.MARK_REFRESH

    def test_manual_lock_preserves(self, session, tenant):
        d = gate(session, tenant, manually_locked=True)
        assert d.outcome == GateOutcome.PRESERVE_CRM

    def test_every_outcome_has_reasons(self, session, tenant):
        for kwargs in [{}, {"confidence": 0.1}, {"crm_value": "CEO"},
                       {"manually_locked": True}, {"staleness_state": StalenessState.STALE}]:
            assert gate(session, tenant, **kwargs).reasons


# ── Simulators ──────────────────────────────────────────────────────────────

@pytest.fixture()
def world(tmp_path):
    from relayiq.providers.simulators import clear_world_cache
    from relayiq.seed.worldgen import generate_world, write_world

    clear_world_cache()
    p = tmp_path / "world.json"
    w = generate_world(seed=7, n_companies=15)
    write_world(w, str(p))
    yield str(p), w
    clear_world_cache()


class TestSimulators:
    def _contact_email(self, w) -> str:
        return next(c["truth"]["work_email"] for c in w["contacts"] if c["truth"]["work_email"])

    def test_deterministic(self, world):
        from relayiq.providers.simulators import make_beta

        path, w = world
        email = self._contact_email(w)
        beta = make_beta(world_path=path, seed=11)
        r1 = beta.enrich("contact", {"work_email": email}, ["job_title", "seniority"])
        r2 = beta.enrich("contact", {"work_email": email}, ["job_title", "seniority"])
        assert r1.outcome == r2.outcome
        assert {f: v.value for f, v in r1.fields.items()} == {f: v.value for f, v in r2.fields.items()}
        assert r1.cost_credits == r2.cost_credits

    def test_cost_charged_only_for_returned_fields(self, world):
        from relayiq.providers.simulators import make_alpha

        path, w = world
        alpha = make_alpha(world_path=path, seed=11, error_rate=0, timeout_rate=0, perm_fail_rate=0)
        r = alpha.enrich("contact", {"work_email": self._contact_email(w)},
                         ["job_title", "seniority", "department"])
        expected = sum(alpha.field_cost("contact", f) for f in r.fields)
        assert r.cost_credits == pytest.approx(expected)

    def test_outage_temp_fail_no_charge(self, world):
        from relayiq.providers.simulators import make_alpha

        path, w = world
        alpha = make_alpha(world_path=path, seed=11, outage=True)
        r = alpha.enrich("contact", {"work_email": self._contact_email(w)}, ["job_title"])
        assert r.outcome == ProviderOutcome.TEMP_FAIL and r.retryable
        assert r.cost_credits == 0.0

    def test_rate_limit(self, world):
        from relayiq.providers.simulators import make_alpha

        path, w = world
        alpha = make_alpha(world_path=path, seed=11, rate_limit_per_minute=1,
                           error_rate=0, timeout_rate=0, perm_fail_rate=0)
        email = self._contact_email(w)
        alpha.enrich("contact", {"work_email": email}, ["job_title"])
        r2 = alpha.enrich("contact", {"work_email": email}, ["job_title"])
        assert r2.outcome == ProviderOutcome.RATE_LIMITED

    def test_unknown_entity_empty_success_zero_cost(self, world):
        from relayiq.providers.simulators import make_beta

        path, _ = world
        beta = make_beta(world_path=path, seed=11, error_rate=0, timeout_rate=0, perm_fail_rate=0)
        r = beta.enrich("contact", {"work_email": "ghost@nowhere.test"}, ["job_title"])
        assert r.outcome == ProviderOutcome.SUCCESS
        assert r.fields == {} and r.cost_credits == 0.0
