# Routing policy — format, strategies, and scoring formulas

Field-level routing (ADR-004) is implemented in
`apps/api/relayiq/engines/routing.py`. A **policy document** — YAML at the API edge,
JSON in the `routing_policies` table — maps `entity.field` to candidate providers and
a strategy, so routing changes are configuration, not code. Every decision persists
its scored candidates, rejections, and factors into `routing_decisions`, making any
selection explainable after the fact.

## Policy format

```yaml
version: 1
defaults:
  strategy: balanced            # cheapest_capable | quality_first | balanced | dynamic
  fallback: true
  max_candidates: 3
  cross_check_stale_fields: [job_title]
fields:
  contact.job_title:   {providers: [beta, alpha], strategy: quality_first}
  contact.seniority:   {providers: [beta, alpha], strategy: quality_first}
  contact.work_email:  {providers: [alpha, beta], strategy: balanced}
  account.root_domain: {providers: [alpha, beta], strategy: cheapest_capable}
  account.employee_count: {providers: [alpha, beta], strategy: cheapest_capable}
```

Resolution per field (`route_fields`): the `fields` entry for
`{entity_type}.{field_name}` wins; otherwise `defaults`. `providers` is an ordered
preference list (defaults to all registered providers); candidates are dropped with a
recorded reason if the provider is not enabled, excluded by budget degradation,
lacks the capability, or has an **open circuit breaker**. Survivors are scored,
sorted, and truncated to `max_candidates`; the top candidate is selected and the rest
become the fallback chain used by the orchestrator's bounded re-routing
(`engines/orchestrator.py::_reroute`, max 4 rounds).

The active policy for a job is the campaign's `routing_policy_id` if set and active,
else the tenant's first active `routing_policies` row, else `DEFAULT_POLICY`
(`orchestrator.py::_active_policy`).

## Candidate scoring (actual formulas from `score_candidate`)

Common inputs per candidate:

- `cost` = `adapter.field_cost(entity_type, field_name)` (credits)
- `quality` = static prior from `FIELD_QUALITY_PRIORS[provider][field]`
  (falls back to the provider's `_default`, then 0.75)
- `error_rate`, `p95` from the latest `provider_health_windows` row
- `health_penalty = min(0.5, error_rate * 2)` — 25% errors halve the score;
  `breaker_open` forces `health_penalty = 1.0`

### 1. `cheapest_capable`

```
score = (1 / max(cost, 0.01)) * (1 - health_penalty) * (1 if quality >= 0.6 else 0.2)
```

Pure price ranking among minimally capable providers; sub-0.6 quality is nearly
disqualifying (×0.2), not merely discounted.

### 2. `quality_first`

```
score = quality * (1 - health_penalty) + 0.05 / max(cost, 0.05)
```

Quality dominates; the small cost term (≤1.0, typically ≪ quality) only breaks ties
between equal-quality providers.

### 3. `balanced` (default)

```
latency_penalty = 0.1 if p95 > 2000 ms else 0.0
score = (quality / max(cost, 0.01) ** 0.5) * (1 - health_penalty) * (1 - latency_penalty)
```

Quality per square-root-credit: the square root makes cost matter sub-linearly, so a
2× price increase must be answered by ~1.41× quality, not 2×.

### 4. `dynamic` (phase 3 — measured to lose at 2-provider scale)

Same combining form as `balanced`, but `quality` is first **blended with observed
history** from `field_observations` (`_dynamic_performance`): with
`observed_precision = selected / total` for this tenant × provider × field, and
`n = min(history_count, 50)` (history under 5 observations is ignored):

```
quality_blended = (quality_prior * (50 - n) + observed_precision * n) / 50
score = (quality_blended / max(cost, 0.01) ** 0.5) * (1 - health_penalty) * (1 - latency_penalty)
```

The prior dominates until ~50 observations of history exist, then observed precision
takes over. **Honest benchmark result** (`docs/benchmarks/results.md`): dynamic
routing came in at 5.94 credits per true usable lead vs 4.07 for the static policy —
its warmup spend was never recouped with only two providers whose strengths a static
table already encodes. It is expected to matter when provider quality drifts or the
provider count grows; that is a hypothesis, not a measured claim.

All factors (`cost`, `quality_prior`, `error_rate`, `p95_latency_ms`,
`breaker_open`, and for dynamic: `observed_precision`, `history_n`,
`quality_blended`) are stored per candidate in `routing_decisions.candidates`.

## `cross_check_stale_fields`

Policy key `defaults.cross_check_stale_fields` (default `["job_title"]`) drives the
staleness cross-check in the orchestrator's `provider_calls` step: when a listed
field came back from exactly **one** provider and its provider-reported age exceeds
the field's `fresh_days` threshold (`services/staleness.py`), RelayIQ buys a second
opinion from the next routed candidate so reconciliation has real cross-provider
evidence instead of trusting a single stale answer. The extra call is recorded in the
ledger and in `routing_decisions.fallback_detail` (`stale_cross_check`,
`primary_age_days`). This is why the full pipeline costs slightly more than static
routing in the benchmark (4.65 vs 4.07 per true usable lead) while raising precision
(0.784 vs 0.773).

## Budget degradation interacting with routing

When a budget crosses its warning threshold (`services/budget.py`), the routing step
applies the budget's `degradation_mode` before scoring
(`orchestrator.py`, routing step):

- `cheapest` → `strategy_override = "cheapest_capable"` for every field
- `cache_only` → no fields are routed at all
- `required_fields_only` → only the campaign's `required_fields` are routed

The override and degradation mode are persisted in the step detail.

## Worked examples

Cheap-first account policy with a quality floor:

```yaml
version: 1
defaults: {strategy: cheapest_capable, max_candidates: 2}
fields:
  account.industry: {providers: [alpha, beta], strategy: balanced}
```

People-quality policy with a wider stale cross-check:

```yaml
version: 1
defaults:
  strategy: quality_first
  cross_check_stale_fields: [job_title, seniority]
fields:
  contact.job_title: {providers: [beta, alpha]}
  contact.seniority: {providers: [beta, alpha]}
  contact.linkedin_url: {providers: [beta, alpha]}
```

Policies are uploaded via the admin API (YAML accepted at the edge, stored as JSON in
`routing_policies.document`) and versioned per tenant; campaigns pin a policy by id.
