# ADR-009: Provider simulation strategy

## Status

Accepted

## Date

2026-07-11

## Context

The product's value claims (redundant-spend avoidance, reconciliation quality,
routing economics) must be *measurable* before any commercial provider contract
exists. Live providers are non-deterministic, rate-limited, paid, and have no ground
truth — you cannot compute precision against reality you don't know. The MVP needs
providers that behave like real ones (errors, latency, staleness, disagreement) while
keeping truth known and runs reproducible.

## Decision

Ship **deterministic simulators over a synthetic world with known truth**, behind the
same adapter contract a real provider would use.

- **Synthetic world** (`relayiq/seed/worldgen.py`): generates companies and contacts
  with a `truth` dict plus **per-provider distorted views**
  (`provider_views.alpha/beta` per record) with controlled accuracy, age
  distributions, and missingness (`PERSONALITIES` table). Wrong values are *plausible*
  (`_wrong_value`: adjacent employee buckets, confusable titles like
  "VP Sales" → "Director of Sales", off-by-one-suffix domains). Company-name variants
  that normalize equal are generated deliberately to exercise normalization-aware
  agreement. All data is synthetic: wordlist names and RFC-2606-reserved `.test`
  domains only. The default world lives at `apps/api/data/synthetic_world.json`
  (`Settings.synthetic_world_path`).
- **Simulators** (`relayiq/providers/simulators.py::SimulatedProvider`): serve values
  from the world file and add provider behavior — per-field costs, gaussian latency,
  error/timeout/permanent-failure rates, extra missingness, an in-process sliding-
  window rate limiter, and a forced-`outage` knob for fallback tests. All randomness
  is seeded per `(seed, provider, entity_key, sorted(fields))` via SHA-256
  (`_rng`), so identical requests behave identically — "which is what makes
  benchmarks and tests fair" (module docstring).
- **Latency is reported, not slept** unless `simulate_latency_sleep=True`: tests and
  benchmarks stay fast while p50/p95/p99 statistics remain meaningful. This is why
  `relayiq_provider_latency_ms` is labeled "(reported, ms)".
- **Personalities**: `make_alpha` (strong/cheap firmographics, low latency, staler
  contact data) and `make_beta` (fresh titles/seniority, ~2× cost, higher latency,
  12% extra missingness) are presets of the same class — the asymmetry is what makes
  field-level routing measurable.
- **Same contract as real providers**: simulators implement `ProviderAdapter`
  (`relayiq/providers/base.py`, "Real provider integrations and simulators share this
  contract (ADR-009)") and are constructed from DB `provider_configs` rows by the
  registry (`relayiq/providers/registry.py`, factories `simulator.alpha` /
  `simulator.beta`), so operators can tune knobs via `provider_configs.config`
  without code changes.
- **Honest labeling**: adapters carry `simulation_mode = True`, surfaced in
  `/v1/admin/providers` and the OpenAPI description ("Providers are SIMULATED in this
  build").

## Alternatives considered

- **Recorded fixtures (VCR-style) of real providers** — replay is deterministic but
  there is still no ground truth to score against, and coverage is frozen to whatever
  was recorded.
- **Mock-only tests, no simulators** — cannot produce end-to-end economics or
  conflict-rate numbers at all.
- **Live sandbox accounts** — none of the target providers offer deterministic
  sandboxes; costs and rate limits make CI infeasible.

## Consequences

- Precision/recall/conflict metrics are **measured against known truth**, never
  fabricated — but they are measurements *of the simulation*: benchmark docs must
  (and do — `relayiq/benchmark/runner.py` header) say provider economics are
  simulated while the control-plane behavior is the real code.
- Deterministic seeds make failures reproducible (`RELAYIQ_PROVIDER_SIM_SEED`,
  default 42).
- Swapping in a real provider is a registry factory + config row, not a refactor
  (see `docs/architecture/provider-sdk.md`).

## Risks

- Simulator personalities are hand-designed; real-provider error modes (schema
  drift, partial matches, entity confusion) are richer than the simulated ones.
- Numbers derived from simulation can be misread as production claims if the
  "simulated" label is dropped in reporting.

## Revisit conditions

- First real provider integration (add contract tests that both real and simulated
  adapters must pass).
- Evidence that simulated distributions diverge materially from observed real-world
  behavior.
