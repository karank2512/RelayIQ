# Provider SDK — adapter contract, simulators, and adding a real provider

The router, orchestrator, ledger, and health tracker never talk to a vendor API
directly — they only see the adapter contract in
`apps/api/relayiq/providers/base.py`. Real integrations and simulators implement the
same interface (ADR-009), which is what lets the benchmark exercise the real control
plane against simulated economics.

## The adapter contract (`providers/base.py`)

```python
class ProviderAdapter(ABC):
    key: str                      # registry key, e.g. "alpha"
    version: str
    display_name: str
    simulation_mode: bool         # True for simulators — surfaced in health()

    def capabilities(self) -> dict[str, set[str]]:
        """{entity_type: {field_name, ...}} the provider can enrich."""

    def field_cost(self, entity_type: str, field_name: str) -> float:
        """Estimated cost in credits for one field."""

    def enrich(self, entity_type, identifiers, fields, *, timeout_ms=8000)
            -> EnrichmentCallResult:
        """One enrichment call. MUST normalize all errors into the result."""
```

Shared helpers provided by the base class: `supports()`, `estimate_cost()`,
`retry_policy()` (default `RetryPolicy(max_retries=2, backoff_base_seconds=0.2,
retry_on=(TEMP_FAIL, TIMEOUT))`), `health()`, and `entity_key()` (contacts key on
`work_email`, accounts on `root_domain`).

**Result types:**

- `ProviderFieldValue` — one field value with provenance: `value`,
  `provider_confidence`, `source_age_days` (provider-reported freshness — feeds the
  staleness engine), `provenance` string.
- `EnrichmentCallResult` — `outcome` (a `ProviderOutcome`: `success | temp_fail |
  perm_fail | timeout | rate_limited | circuit_open`), `fields`
  (`{name: ProviderFieldValue}`), `latency_ms`, `cost_credits`, `error`,
  `raw_payload`, `retryable`.

**The one hard rule: `enrich()` never raises.** Timeouts, 5xxs, rate limits, and bad
requests are all normalized into the result's `outcome`/`retryable` fields. The
execution layer (`services/provider_exec.py::execute_with_retries`) implements
bounded retries with exponential backoff for retryable outcomes, feeds the
per-provider circuit breaker (`providers/registry.py::CircuitBreaker` — opens after
5 consecutive retryable failures, half-opens after 30 s), persists a
`ProviderRequest` (+ `ProviderResponse` when a payload exists — ADR-012), updates the
5-minute `provider_health_windows` aggregates, and emits
`relayiq_provider_calls_total` / `relayiq_provider_latency_ms` /
`relayiq_provider_retries_total` metrics.

## Registry (`providers/registry.py`)

Adapters are built **data-driven** from `provider_configs` rows: `adapter` names a
factory in `_FACTORIES` (currently `simulator.alpha`, `simulator.beta`), and the
row's `config` JSON plus `provider_capabilities` rows (field costs + capability sets)
are passed as constructor overrides. Operators can therefore retune costs, error
rates, or capabilities — or disable a provider — without code changes. The registry
also owns circuit-breaker state; `registry.available(key)` = adapter enabled AND
breaker not open.

## Simulators (`providers/simulators.py`)

`SimulatedProvider` serves values from the synthetic world file
(`relayiq/seed/worldgen.py`) — each world entity carries `provider_views` per
provider (value, age_days, per-provider errors/staleness baked in by the world
generator) plus known `truth` for scoring. All randomness is seeded per
`(seed, provider, entity, sorted(fields))` via SHA-256, so identical requests behave
identically — this determinism is what makes benchmarks and tests fair and
reproducible.

**Simulator knobs (constructor args / `provider_configs.config`):**

| Knob | Default | Effect |
|---|---|---|
| `capabilities` | — | `{entity_type: {fields}}` the simulator will answer |
| `field_costs` / `default_field_cost` | — / 1.0 | credits charged **per returned field** |
| `latency_base_ms` / `latency_jitter_ms` | 120 / 80 | Gaussian reported latency |
| `error_rate` | 0.02 | probability of `temp_fail` (simulated 5xx) |
| `timeout_rate` | 0.01 | probability of `timeout` (also triggered if latency > timeout_ms) |
| `perm_fail_rate` | 0.005 | probability of non-retryable `perm_fail` |
| `extra_missing_rate` | 0.0 | extra per-field coverage gaps beyond the world's own missingness |
| `rate_limit_per_minute` | None | in-process sliding-window limiter → `rate_limited` |
| `provider_confidence_base` | 0.85 | mean of reported provider confidence (Gaussian, clamped 0.3–0.99) |
| `seed` / `world_path` | settings | determinism + world source |
| `simulate_latency_sleep` | False | latency is *reported*, not slept, unless set — tests stay fast while p50/p95/p99 stay meaningful (ADR-009) |
| `outage` | False | force `temp_fail` on every call (outage/fallback tests) |

The in-process rate limiter is a **documented limitation**: it is per adapter
instance, not shared across workers — a real deployment would move it to Redis
(SECURITY.md §3).

## Personalities

Two presets exercise the routing problem realistically (their asymmetry is why
field-level routing wins in the benchmark):

- **Alpha** (`make_alpha`) — strong company coverage, moderate cost, low latency
  (base 110 ms), staler contact titles. Costs e.g. `root_domain` 0.5,
  `employee_count` 0.8, `job_title` 1.0, `work_email` 1.5, default 0.6.
  `error_rate` 0.02, confidence base 0.86.
- **Beta** (`make_beta`) — fresh contact titles/seniority, higher cost and latency
  (base 340 ms), coverage gaps (`extra_missing_rate` 0.12). Costs e.g. `job_title`
  2.0, `seniority` 1.2, `work_email` 2.2, default 1.2. `error_rate` 0.035,
  confidence base 0.9.

Measured consequence (`docs/benchmarks/results.md`, seeded synthetic run): routing
people-fields to Beta and firmographics to Alpha improved *both* cost and quality —
cost per true usable lead 13.24 (naive) → 4.07 (static routing) and field precision
0.682 → 0.773.

## Adding a real provider

1. **Implement the adapter** in `relayiq/providers/` subclassing `ProviderAdapter`:
   set `simulation_mode = False`; map vendor errors to `ProviderOutcome` values and
   set `retryable` correctly (this drives retries, breakers, and health); populate
   `ProviderFieldValue.source_age_days` if the vendor reports freshness (feeds
   staleness cross-checks); return per-returned-field `cost_credits`.
2. **Respect ADR-012**: do not persist full vendor payloads in
   `EnrichmentCallResult.raw_payload` — return a normalized summary, or ship the
   TTL/encryption handling with the adapter.
3. **Register a factory**: add `"vendor.name": make_vendor` to
   `providers/registry.py::_FACTORIES`. Credentials come from the environment
   (`relayiq/config.py`), never from `provider_configs.config`.
4. **Insert configuration**: a `provider_configs` row (key, adapter, timeout,
   retries, `reliability_prior`) and `provider_capabilities` rows (entity_type ×
   field → cost, quality prior). The registry picks them up on the next
   `get_registry(session, refresh=True)`.
5. **Add routing priors**: extend `FIELD_QUALITY_PRIORS` in `engines/routing.py`
   (or rely on `_default`) and reference the provider in routing policy documents.
6. **Test like the simulators are tested**: contract tests for error normalization
   (every vendor failure class → correct outcome, never an exception) and
   fixture-based response mapping; wire an `outage`-style toggle for fallback tests.

No live provider integration exists in this build — Alpha/Beta are simulators, and
that status is stated in the README's integration table.
