# Interview guide — implementation-grounded Q&A

Twelve questions an interviewer is likely to ask about RelayIQ, answered from the actual
code and the measured results. Every module path is real; every number comes from
`docs/benchmarks/` (seeded synthetic benchmark — simulated providers, real control-plane
code) and is labeled as such. Nothing here claims live-provider or production validation.

---

## 1. How does RelayIQ decide which provider answers which field?

Routing is policy-driven and per-field (`relayiq/engines/routing.py`, ADR-004). A YAML/JSON
policy maps `entity.field` → candidate providers plus a strategy: `cheapest_capable`,
`quality_first`, `balanced`, or `dynamic`. Each candidate is scored from its per-field cost
(`adapter.field_cost`), a static field-quality prior (`FIELD_QUALITY_PRIORS` — e.g. alpha
0.92 on `root_domain` but 0.70 on `job_title`; beta 0.93 on `job_title`), and a health
penalty derived from the latest `ProviderHealthWindow` (25% error rate halves the score;
an open circuit breaker rejects the candidate outright). The strategies weight these
differently — `cheapest_capable` is inverse-cost with a quality floor, `quality_first` is
quality-dominant, `balanced` divides quality by √cost. The default policy sends company
fields to the cheap firmographics provider and people fields (`job_title`, `seniority`,
`department`, `linkedin_url`) to the fresh-people provider. Every decision persists its
candidates, scores, rejections, and factors to `routing_decisions`, so any selection is
explainable after the fact — visible in the lineage UI. Measured effect on the seeded
synthetic benchmark (`docs/benchmarks/results.md`): static field routing raised field
precision from 0.682 to 0.773 while cutting cost per true usable lead from 13.24 to 4.07
credits — routing improved cost *and* quality simultaneously, because each field is bought
from the provider that is actually good at it.

## 2. Why should anyone trust your confidence score?

They should trust it exactly as far as we measured it — and we published the measurement.
The score (`relayiq/engines/confidence.py`, formula `rules-v1`, ADR-005) is a weighted
mean of explicit components: provider/field prior (0.25), freshness decay (0.20,
`exp(-ln2·age/stale_days)` from `services/staleness.py`), cross-provider agreement (0.20),
format validity (0.10), cross-field consistency (0.10), provider-native confidence (0.05),
and review history (0.10), minus 0.25× conflict severity. Weights of unavailable
components are redistributed, and every scored field stores its full component breakdown,
inspectable in the lineage UI. Then we tested it against synthetic ground truth
(`docs/benchmarks/calibration.md`): Brier 0.1642, ECE 0.0905, with clear overconfidence
above 0.8 (the 0.9–1.0 bucket has mean confidence 0.92 but accuracy 0.81). So the honest
claim, stated in the report and the README, is that rules-v1 is a **ranking signal, not a
probability**: higher scores are more likely correct, which is enough to order the review
queue and set gate thresholds, but a 0.9 does not mean 90%. That documented limitation is
itself the trust argument — the system tells you precisely what its number is and is not,
and the learned-calibration replacement is a roadmap item with ECE 0.0905 as the bar.

## 3. What happens when the same webhook is delivered twice?

Nothing is spent twice — enforced at three layers, and asserted by e2e test 8
(`tests/e2e/test_scenarios.py::test_e2e_08_duplicate_webhook_no_double_spend`). First,
authenticity: `relayiq/services/webhook_security.py` verifies a Stripe-style
`t=<ts>,v1=<hex>` HMAC-SHA256 over the raw body before any parsing, using
`hmac.compare_digest` without short-circuiting across rotated secrets, plus a ±replay
window on the timestamp. Second, delivery dedup: `relayiq/api/routers/webhooks.py` inserts
a `WebhookDelivery` row under a unique constraint on (tenant, source, delivery_id); a
replay hits `IntegrityError`, and the handler returns HTTP 200 with `duplicate: true` and
the **original** `job_id` — no second job, no provider calls. Returning 200 matters:
webhook senders retry on non-2xx, so the correct answer to "I already have this" is
success. Third, durable idempotency (`relayiq/services/idempotency.py`, ADR-007) covers
the general case beyond webhooks: claims are atomic via a DB unique constraint on
(tenant, scope, key) — the only mechanism safe under concurrent identical requests and
worker restarts — and completed requests replay their stored response snapshot. The test
asserts the replayed delivery returns the same job and that the cost ledger's total for
that job is unchanged. Measured under load: idempotent replays served at p50 12 ms.

## 4. What does a human reviewer actually see when deciding?

Everything the machine used, plus the power to disagree — see `ReviewDetailPage`
(`apps/dashboard/src/pages/ReviewDetail.tsx`) backed by `/v1/review/tasks/{id}`. The
reviewer gets: the task's reason and confidence bar with the suggested value; the
**original record** as submitted; a "Why this needs review" panel quoting the
reconciliation engine's prose reasoning and its factors JSON; and every provider
observation as a card — value, provider, cost, source age, provider-native confidence,
staleness state — with the machine-suggested one badged. Actions are `accept_suggested`,
`select_observation` ("use this value" on any card), `correct_value` (free-text
correction), `reject`, `defer`, or note-only, each recorded with an audit note. Resolved
tasks expose **Reverse decision** behind a confirm dialog; reversal appends to history
rather than deleting anything, and each decision row stores the previous state snapshot
(`services/review.py::_snapshot`). Queue ordering is informative too: task priority is
boosted by staleness (`review_priority_boost`) and lowered confidence, so reviewers see
the riskiest items first. The design bet is that reviewer time is the scarcest resource
in the pipeline, so the UI's job is to make one decision cheap: both candidate values,
why they conflict, and what it costs to be wrong, on one screen.

## 5. How do you quantify ROI for a customer?

By making cost-per-usable-lead a query instead of an estimate. The cost ledger
(`relayiq/services/ledger.py`) writes one row per attempted cost-bearing operation —
including cache hits, which record the `avoided_cost` they did *not* spend — and flags
each entry with whether its result was later accepted (`mark_acceptance`) and whether it
paid for stale data. `cost_per()` then computes spend per attempted record, per accepted
record, and per usable lead, where "usable lead" is a configurable definition
(`relayiq/config.py`, `docs/benchmarks/metric-definitions.md`), not a vanity count. The
Analytics page surfaces these directly: "Redundant spend avoided — measured, not
estimated." The benchmark (`docs/benchmarks/results.md`, seeded synthetic providers, real
control-plane code) shows the shape of the win: a naive call-everyone strategy costs
13.24 credits per true usable lead; the full RelayIQ pipeline gets the same workload to
4.65 (static routing alone: 4.07), while raising field precision from 0.682 to 0.784. The
honest framing for a customer is: your ratio will differ — provider economics here are
simulator parameters — which is exactly why the product's core artifact is a ledger that
computes *your* number continuously rather than a benchmark chart.

## 6. How do you handle stale data?

Staleness is a first-class state machine, not a TTL. `relayiq/services/staleness.py`
classifies every canonical field as fresh / aging / stale / expired / unknown against
per-field thresholds (a job title expires past 90 days; a root domain survives to 730)
with a tenant-policy > global-policy > builtin-default precedence. That state feeds five
mechanisms. Cache and store reuse: `is_reusable()` serves fresh/aging values without
re-enrichment, treats expired/unknown as missing, and stale triggers refresh — e2e test 9
verifies an aged value forces a real provider call. Confidence: freshness decays
exponentially (`freshness_factor`, half-life at `stale_days`) inside the rules-v1 formula,
so old answers rank lower automatically. Review: stale and expired items get priority
boosts in the queue. CRM gate: expired values are never pushed — they get `mark_refresh`
instead of a write. And most distinctively, the **stale cross-check** in
`relayiq/engines/orchestrator.py`: when the primary provider's answer for a configured
high-risk field (default `job_title`) comes back outside its freshness window, the
orchestrator buys a second opinion from the next routing candidate and reconciles the two.
On the benchmark this is why the full pipeline costs slightly more than static routing
(1,097.8 vs 987.8 credits) — it spends ~11% extra to push precision from 0.773 to 0.784,
a tradeoff the ledger makes visible rather than hiding.

## 7. Why a sidecar next to Clay instead of replacing it?

Because Clay is a workflow builder with real user lock-in, and RelayIQ's value is
orthogonal: it is the decision layer Clay doesn't have. Clay answers "how do I build an
enrichment table"; RelayIQ answers "should this credit be spent, by which provider, and
should the result touch the CRM" — pre-decision, field-level routing, reconciliation,
confidence, review, and gating (the six roles in the README). Replacing Clay means
competing on table UX, integrations, and templates; sitting beside it means Clay's
generic HTTP-API column simply calls RelayIQ's endpoint and gets back a decision-annotated
result, so users keep their workflows and gain a control plane. The integration contract
is implemented to match that column's semantics (`docs/architecture/clay-integration.md`),
including idempotent replays for Clay's re-run behavior — but honestly labeled: it has
**not** been live-tested against Clay, and the doc lists the unverified assumptions. The
same logic applies downstream: RelayIQ does not replace HubSpot, it gates writes into it.
Strategically, a sidecar also degrades gracefully — a team can route one campaign's
traffic through RelayIQ, compare its ledger against their status quo, and expand or rip
it out without migrating anything, which is exactly the wedge a pilot needs.

## 8. How do you protect the customer's CRM from bad data?

Every field must earn its write (`relayiq/services/crm_gate.py`, ADR-008). The gate
evaluates each field independently and returns one of six outcomes with stored prose
reasons: `write`, `no_write`, `secondary_property`, `require_approval`, `preserve_crm`,
`mark_refresh`. The ordering encodes the safety policy: policy blocks and reviewer
rejections short-circuit first; unresolved provider conflicts can never reach the CRM
without review sign-off; confidence below the threshold (default 0.6) requires approval
unless a human already accepted the value (human judgment overrides the score); expired
or stale values get `mark_refresh` rather than a push. The most interesting outcome is
the fresh-CRM comparison: if the CRM already holds a fresh value that differs from ours,
we never silently overwrite it — high-confidence enrichment (≥0.85) is written to a
**secondary property** for operator comparison, and anything less preserves the CRM value
outright. E2e test 12 (`test_e2e_12_low_confidence_does_not_overwrite_crm`) pre-populates
the simulated CRM with a fresh value and asserts a modest-confidence enrichment lands as
`preserve_crm`/`secondary_property`, never a silent overwrite. The dashboard's CRM page
shows before/after and the gate's reasons per field, and the simulator tab lets a reviewer
verify gated fields did *not* land — the negative case is observable, not assumed.

## 9. What cost/latency/quality tradeoffs did you make, and how did you measure them?

The measured ones are the honest centerpiece. Quality-for-cost: the stale cross-check
buys a second opinion on stale `job_title` answers; on the benchmark that added ~11% spend
(987.8 → 1,097.8 credits vs static routing) for about one point of precision (0.773 →
0.784). Whether that's worth it is a per-tenant policy decision — which is why it's a
routing-policy knob, not a constant. Review conservatism: the full pipeline routes ~5% of
records to human review and the benchmark *excludes* those from its usable-lead count
(236 vs static's 243), so RelayIQ's headline number is deliberately understated rather
than assuming reviewers rubber-stamp. Cost-for-nothing: dynamic routing lost outright
(5.94 credits/usable lead) — we report that rather than tuning until it won. Latency:
sync mode runs the whole pipeline inline, persisting ~9 decision records per job; the
load test (`docs/benchmarks/load-test-results.md`, dev laptop) measured p50 32 ms / p95
580 ms overall, with fresh full-pipeline enrichments at p50 120 ms and the p95 tail
dominated by cross-check jobs — the quality feature *is* the latency tail, visibly.
Idempotent replays cost 12 ms p50 and zero credits. The design stance: make each tradeoff
a recorded, per-field decision with its cost in the ledger, so the tradeoff curve is
data, not opinion.

## 10. How would this handle 10× the load?

The architecture already separates the request path from the pipeline: `mode: "async"`
enqueues jobs to Celery workers (`relayiq/workers/`), so the API's job is an idempotency
claim plus a row insert, and enrichment throughput scales with worker count. The measured
baseline is deliberately worst-case — 35.4 req/s with 0 failures on a single uvicorn
process on a laptop, running sync-mode pipelines inline — so 10× is primarily a
deployment change, with three real engineering items on the roadmap. First, the rate
limiter and circuit breaker are currently per-process (`providers/registry.py::
CircuitBreaker`, the sliding-window limiter in the simulators); N workers would each
learn about a struggling provider independently and collectively overrun vendor limits,
so both need Redis-backed shared state. Second, the append-only tables (cost ledger,
field observations — never overwritten by design, ADR-006) grow linearly with traffic;
ADR-002 and ADR-006 already flag time-based partitioning and archival as the revisit
condition. Third, the Analytics and Overview endpoints are aggregate queries over those
same tables; at 10× they move to read replicas or materialized rollups so dashboards
can't contend with the write path. What does *not* need to change is the correctness
core: idempotency and budget reservation are DB-constraint-based (unique constraints,
single guarded UPDATE), which are exactly the primitives that stay safe under horizontal
concurrency — the integration suite includes concurrency tests for them.

## 11. How do you evaluate whether a provider is any good?

Continuously, from persisted evidence, at field granularity. Operationally,
`ProviderHealthWindow` rows aggregate recent request counts, success rates, p95 latency,
and rate-limit hits; the router reads the latest window and converts error rate into a
score penalty (25% errors halve a candidate's score), and an open circuit breaker removes
the provider from candidacy with the rejection recorded. Qualitatively, every provider
answer is stored as a `FieldObservation` that is never overwritten; reconciliation and
review then mark each observation `is_selected` or `is_rejected`. That yields a measured,
per-provider-per-field acceptance record: the Analytics page's "Provider × field
performance" table shows observations, selected share, and rejected share — effectively
reviewed precision per field, since human review decisions flow into the same flags. The
`dynamic` routing strategy closes the loop by blending the static quality prior with this
observed precision, weighted by history volume (capped at 50 observations) so thin history
doesn't swamp the prior. The benchmark validated the evaluation *and* its limits: with
only two providers, dynamic routing's warmup exploration cost more than its learning
recouped (5.94 vs static's 4.07 credits per usable lead) — an honest negative result that
says field-level evaluation is worth storing from day one, but *acting* on it dynamically
needs more providers to choose among (see roadmap: contextual bandit at >2 providers).

## 12. What's missing before production?

Listed in the README and SECURITY.md rather than discovered by a customer. Auth: the dev
JWT login (seeded users, password auth) is a documented substitute for real OAuth/OIDC
SSO with token rotation and MFA — fine for a demo tenant, not for a customer's GTM data.
Integrations: providers are simulators; the HubSpot adapter is implemented and
fixture-tested but **live sync is unverified**; the Clay contract is implemented but not
live-tested; Salesforce is designed only. Nothing real has been written to a real CRM,
and the roadmap's first item is exactly that verification. Compliance and security: the
threat model exists and mechanisms are tested (HMAC, SSRF guards, tenant scoping,
redaction), but there has been no external security review or pen test, and processing
real personal data needs a GDPR/CCPA pass — the pilot kit's consent/data-handling doc
covers interviews, not production PII. Calibration: rules-v1 is measurably miscalibrated
(ECE 0.0905, overconfident above 0.8); shipping confidence-driven automation to customers
warrants the learned calibration layer with that number as the bar to beat. Scale
hygiene: Redis-backed rate limiting/breakers, ledger partitioning, a reconciliation sweep
for jobs stranded by broker loss (ADR-001 documents this gap), and durable scheduling for
`mark_refresh`. The honest summary: the decision core is real and measured; the
edges that touch other people's systems and data are where the remaining work lives.
