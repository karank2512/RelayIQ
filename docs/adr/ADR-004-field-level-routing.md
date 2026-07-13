# ADR-004: Field-level provider routing

## Status

Accepted

## Date

2026-07-11

## Context

Providers are good at different things at different prices (the simulators encode
this deliberately: Alpha is strong/cheap on firmographics, Beta is fresh/expensive on
contact titles — `relayiq/providers/simulators.py`). Routing a whole record to one
provider forces a single trade-off across all fields. Routing must also be
explainable: "why did this field cost 2.0 credits from Beta?" has to be answerable
later.

## Decision

Route **per field**, under a declarative policy document, with every decision
persisted. Implemented in `relayiq/engines/routing.py`.

- **Policy documents** live in `routing_policies.document` (JSON in the DB; YAML is
  accepted at the admin API edge and parsed with `yaml.safe_load` —
  `relayiq/api/routers/admin.py`). Shape (from the module docstring and
  `DEFAULT_POLICY`):

  ```yaml
  version: 1
  defaults: {strategy: balanced, fallback: true, max_candidates: 3}
  fields:
    contact.job_title:   {providers: [beta, alpha], strategy: quality_first}
    account.root_domain: {providers: [alpha, beta], strategy: cheapest_capable}
  ```

- **Selection** (`route_fields`): for each field, candidate providers are filtered
  (enabled in registry, supports the field, not excluded by budget degradation, and
  circuit breaker closed — open breakers are recorded in `rejected` with reason
  "circuit breaker open"), scored by the strategy (see
  `docs/architecture/routing-policy.md` for the exact formulas in
  `score_candidate`), sorted, truncated to `max_candidates`, and the top candidate is
  selected. Everything — candidates with factor breakdowns, rejected providers with
  reasons, strategy, expected cost — is written to a `routing_decisions` row
  (`relayiq/engines/orchestrator.py`, routing step).
- **Fallback** is field-level too: `group_by_provider` batches selected fields into
  one call per provider; fields a provider fails to return are re-planned to their
  next untried candidate (`orchestrator.py::_reroute`), bounded at 4 rounds
  ("no retry storms") with an `attempted_pairs` set preventing repeat
  (provider, field) attempts. Fallback use is stamped on the routing row
  (`fallback_used`, `fallback_detail`).
- **Budget coupling**: when a budget crosses its warning threshold, the degradation
  mode can override the strategy to `cheapest_capable`, drop optional fields
  (`required_fields_only`), or suppress provider calls entirely (`cache_only`) —
  orchestrator routing step.

## Alternatives considered

- **Record-level routing** (one provider per record) — cannot express "Beta for
  titles, Alpha for firmographics"; measurably worse economics on the simulated
  benchmark (`relayiq/benchmark/runner.py` compares these strategies).
- **Hardcoded routing table** — the default policy *is* a hardcoded fallback
  (`DEFAULT_POLICY`), but tenants/campaigns need per-campaign policies without code
  changes (`campaigns.routing_policy_id`, `_active_policy` in the orchestrator).
- **Bandit/ML routing** — the `dynamic` strategy is a deliberate middle step: blend
  static priors with observed per-field acceptance from `field_observations`
  (`_dynamic_performance`, min 5 observations, linear blend capped at 50) instead of
  a full bandit, keeping decisions explainable.

## Consequences

- Every field's provider choice is reconstructable from `routing_decisions`
  (surfaced by `relayiq/services/lineage.py::field_lineage`).
- One provider call per provider per job round (batching), not per field.
- Policy mistakes (e.g. routing a field to a provider that does not support it)
  degrade gracefully: the provider lands in `rejected` with reason
  `does not support {key}` and the next candidate serves.

## Risks

- Scores from different strategies are not comparable to each other; strategy
  overrides mid-flight (budget degradation) change ranking semantics.
- `_recent_health` reads only the latest `provider_health_windows` row — a single
  quiet window can mask a bad streak.
- Static quality priors (`FIELD_QUALITY_PRIORS`) mirror the *simulator*
  personalities; real providers will need re-derived priors.

## Revisit conditions

- Real provider integrations (priors must come from measured history, not the
  simulator mirror).
- Evidence that the `dynamic` blend is too slow to adapt (raise the blend cap or move
  to a proper bandit).
- Policy documents needing per-tenant validation/schema versioning beyond `version: 1`.
