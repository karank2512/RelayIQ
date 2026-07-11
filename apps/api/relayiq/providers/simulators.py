"""Deterministic provider simulators.

They serve values from the synthetic world file (see relayiq/seed/worldgen.py) and add
provider behavior on top: cost, latency, error rates, missingness, rate limits, temporary
and permanent failures. All randomness is seeded per (seed, provider, entity, fields) so
identical requests behave identically — which is what makes benchmarks and tests fair.

Simulated latency is *reported*, not slept, unless `simulate_latency_sleep` is set —
keeping tests and benchmarks fast while p50/p95/p99 metrics stay meaningful (ADR-009).
"""

import hashlib
import json
import random
import threading
import time
from pathlib import Path
from typing import Any

from relayiq.config import get_settings
from relayiq.enums import EntityType, ProviderOutcome
from relayiq.providers.base import EnrichmentCallResult, ProviderAdapter, ProviderFieldValue

_world_cache: dict[str, dict] = {}
_world_lock = threading.Lock()


def load_world(path: str | None = None) -> dict:
    """Load and index the synthetic world file (cached per path)."""
    p = path or get_settings().synthetic_world_path
    with _world_lock:
        if p in _world_cache:
            return _world_cache[p]
        raw = json.loads(Path(p).read_text())
        index: dict[str, dict] = {"companies": {}, "contacts": {}, "raw": raw}
        for c in raw.get("companies", []):
            index["companies"][c["world_id"]] = c
            domain = (c["truth"].get("root_domain") or "").lower()
            if domain:
                index["companies"].setdefault(domain, c)
        for ct in raw.get("contacts", []):
            index["contacts"][ct["world_id"]] = ct
            email = (ct["truth"].get("work_email") or "").lower()
            if email:
                index["contacts"].setdefault(email, ct)
        _world_cache[p] = index
        return index


def clear_world_cache() -> None:
    with _world_lock:
        _world_cache.clear()


class _SlidingWindowLimiter:
    """In-process rate limiter (per provider instance). Documented limitation:
    not shared across workers — real deployments would move this to Redis."""

    def __init__(self, per_minute: int | None):
        self.per_minute = per_minute
        self._events: list[float] = []
        self._lock = threading.Lock()

    def allow(self) -> bool:
        if not self.per_minute:
            return True
        now = time.monotonic()
        with self._lock:
            self._events = [t for t in self._events if now - t < 60]
            if len(self._events) >= self.per_minute:
                return False
            self._events.append(now)
            return True


class SimulatedProvider(ProviderAdapter):
    """Configurable simulator; Alpha/Beta are two personality presets of this class."""

    simulation_mode = True

    def __init__(
        self,
        key: str,
        display_name: str,
        capabilities: dict[str, set[str]],
        field_costs: dict[str, float],
        *,
        default_field_cost: float = 1.0,
        latency_base_ms: float = 120,
        latency_jitter_ms: float = 80,
        error_rate: float = 0.02,
        timeout_rate: float = 0.01,
        perm_fail_rate: float = 0.005,
        extra_missing_rate: float = 0.0,
        rate_limit_per_minute: int | None = None,
        provider_confidence_base: float = 0.85,
        seed: int | None = None,
        world_path: str | None = None,
        simulate_latency_sleep: bool = False,
        outage: bool = False,
    ):
        self.key = key
        self.display_name = display_name
        self._capabilities = capabilities
        self._field_costs = field_costs
        self._default_field_cost = default_field_cost
        self.latency_base_ms = latency_base_ms
        self.latency_jitter_ms = latency_jitter_ms
        self.error_rate = error_rate
        self.timeout_rate = timeout_rate
        self.perm_fail_rate = perm_fail_rate
        self.extra_missing_rate = extra_missing_rate
        self.provider_confidence_base = provider_confidence_base
        self.seed = seed if seed is not None else get_settings().provider_sim_seed
        self.world_path = world_path
        self.simulate_latency_sleep = simulate_latency_sleep
        self.outage = outage  # force temp failures (for outage/fallback tests)
        self._limiter = _SlidingWindowLimiter(rate_limit_per_minute)

    # -- ProviderAdapter interface ----------------------------------------

    def capabilities(self) -> dict[str, set[str]]:
        return self._capabilities

    def field_cost(self, entity_type: str, field_name: str) -> float:
        return self._field_costs.get(field_name, self._default_field_cost)

    def enrich(
        self,
        entity_type: str,
        identifiers: dict[str, str],
        fields: list[str],
        *,
        timeout_ms: int = 8000,
    ) -> EnrichmentCallResult:
        entity_key = self.entity_key(entity_type, identifiers)
        rng = self._rng(entity_key, fields)
        latency = max(5.0, rng.gauss(self.latency_base_ms, self.latency_jitter_ms / 2))
        if self.simulate_latency_sleep:
            time.sleep(latency / 1000)

        def _fail(outcome: ProviderOutcome, error: str, retryable: bool) -> EnrichmentCallResult:
            return EnrichmentCallResult(
                provider_key=self.key,
                entity_type=entity_type,
                outcome=outcome,
                latency_ms=round(latency, 1),
                cost_credits=0.0,
                error=error,
                retryable=retryable,
            )

        if not self._limiter.allow():
            return _fail(ProviderOutcome.RATE_LIMITED, "rate limit exceeded", retryable=True)
        if self.outage or rng.random() < self.error_rate:
            return _fail(ProviderOutcome.TEMP_FAIL, "simulated upstream 5xx", retryable=True)
        if rng.random() < self.timeout_rate or latency > timeout_ms:
            return _fail(ProviderOutcome.TIMEOUT, "simulated timeout", retryable=True)
        if rng.random() < self.perm_fail_rate:
            return _fail(ProviderOutcome.PERM_FAIL, "simulated permanent error (bad request)", retryable=False)

        record = self._lookup(entity_type, entity_key)
        values: dict[str, ProviderFieldValue] = {}
        raw: dict[str, Any] = {}
        cost = 0.0
        if record is not None:
            views = record.get("provider_views", {}).get(self.key, {})
            for f in fields:
                if not self.supports(entity_type, f):
                    continue
                view = views.get(f)
                if view is None or view.get("value") is None:
                    continue
                if self.extra_missing_rate and rng.random() < self.extra_missing_rate:
                    continue  # simulated coverage gap beyond the world file's own missingness
                conf = min(0.99, max(0.3, rng.gauss(self.provider_confidence_base, 0.06)))
                values[f] = ProviderFieldValue(
                    field_name=f,
                    value=view["value"],
                    provider_confidence=round(conf, 3),
                    source_age_days=view.get("age_days"),
                    provenance=f"{self.key}:world:{record['world_id']}",
                )
                raw[f] = view["value"]
                cost += self.field_cost(entity_type, f)  # charge per returned field

        return EnrichmentCallResult(
            provider_key=self.key,
            entity_type=entity_type,
            outcome=ProviderOutcome.SUCCESS,
            fields=values,
            latency_ms=round(latency, 1),
            cost_credits=round(cost, 4),
            raw_payload={"match": record is not None, "fields": raw},
        )

    # -- internals ---------------------------------------------------------

    def _rng(self, entity_key: str, fields: list[str]) -> random.Random:
        material = f"{self.seed}:{self.key}:{entity_key}:{','.join(sorted(fields))}"
        digest = hashlib.sha256(material.encode()).hexdigest()
        return random.Random(int(digest[:16], 16))

    def _lookup(self, entity_type: str, entity_key: str) -> dict | None:
        if not entity_key:
            return None
        world = load_world(self.world_path)
        bucket = "contacts" if entity_type == EntityType.CONTACT.value else "companies"
        return world[bucket].get(entity_key)


# ── Personality presets (ADR-009) ──────────────────────────────────────────

ACCOUNT_FIELDS = {
    "name", "website", "root_domain", "linkedin_url", "industry", "sub_industry",
    "employee_count", "annual_revenue_usd", "hq_city", "hq_state", "hq_country",
    "company_type", "founded_year", "technology_signals",
}
CONTACT_FIELDS = {
    "first_name", "last_name", "full_name", "work_email", "job_title", "seniority",
    "department", "country", "linkedin_url",
}


def make_alpha(**overrides) -> SimulatedProvider:
    """Alpha: strong company coverage, moderate cost, low latency, staler contact titles."""
    kwargs: dict = dict(
        capabilities={"account": set(ACCOUNT_FIELDS), "contact": set(CONTACT_FIELDS)},
        field_costs={
            "root_domain": 0.5, "website": 0.5, "employee_count": 0.8, "industry": 0.6,
            "annual_revenue_usd": 1.2, "job_title": 1.0, "seniority": 0.5, "work_email": 1.5,
        },
        default_field_cost=0.6,
        latency_base_ms=110,
        latency_jitter_ms=60,
        error_rate=0.02,
        timeout_rate=0.008,
        provider_confidence_base=0.86,
    )
    kwargs.update(overrides)
    return SimulatedProvider("alpha", "Provider Alpha (simulated)", **kwargs)


def make_beta(**overrides) -> SimulatedProvider:
    """Beta: strong fresh contact titles/seniority, higher cost & latency, gaps on company fields."""
    kwargs: dict = dict(
        capabilities={"account": set(ACCOUNT_FIELDS), "contact": set(CONTACT_FIELDS)},
        field_costs={
            "job_title": 2.0, "seniority": 1.2, "department": 1.0, "work_email": 2.2,
            "linkedin_url": 1.0, "root_domain": 1.0, "employee_count": 1.5, "industry": 1.2,
        },
        default_field_cost=1.2,
        latency_base_ms=340,
        latency_jitter_ms=180,
        error_rate=0.035,
        timeout_rate=0.015,
        extra_missing_rate=0.12,
        provider_confidence_base=0.9,
    )
    kwargs.update(overrides)
    return SimulatedProvider("beta", "Provider Beta (simulated)", **kwargs)
