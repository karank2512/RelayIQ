# RelayIQ roadmap

Prioritized by how directly each item converts the current build — real control-plane
code measured against simulated providers — into something a customer can run against
their live stack. Each item states why it's next and what "done" means. Measured
baselines referenced below come from `docs/benchmarks/` and are seeded-synthetic numbers.

## P0 — credibility: make one integration claim real

### 1. Live HubSpot verification

**Why first:** the HubSpot adapter (v3 objects API, rate-limit/retry handling, dry-run)
is implemented and fixture-tested, but live sync is explicitly **not verified** — the
README says so. Every downstream claim about CRM protection gets 10× more credible the
day one real HubSpot portal has been written to and read back. This is the cheapest item
on the list with the highest trust payoff.

**Acceptance criteria:**
- Against a real HubSpot developer/sandbox portal with `HUBSPOT_ACCESS_TOKEN` set: create,
  update, and read back a contact and a company through the adapter.
- Gate outcomes verified live: a `write` lands, a `secondary_property` creates the
  secondary field, `preserve_crm`/`no_write` provably do not modify the portal.
- 429 rate-limit handling exercised against the real API (not just fixtures).
- Integration-status table in README updated from "live sync not verified" to a dated
  "verified against sandbox portal" — with any discovered contract mismatches documented.

### 2. Clay live test

**Why:** the sidecar HTTP contract matches Clay's generic HTTP-API column on paper
(`docs/architecture/clay-integration.md` lists the unverified assumptions), and the
sidecar-not-replacement positioning depends on it actually working from inside a Clay
table — including Clay's re-run behavior hitting the idempotent-replay path.

**Acceptance criteria:**
- A real Clay table column calls `POST /v1/enrichment/execute` (or the webhook path) and
  renders returned fields.
- Re-running the Clay table does not double-spend: replays observed in the cost ledger as
  avoided cost, `duplicate`/replay semantics confirmed from Clay's side.
- Each assumption in `clay-integration.md` marked verified or corrected.

## P1 — the measured-quality gaps

### 3. Learned confidence model (beat ECE 0.0905)

**Why:** rules-v1 is honestly documented as a ranking signal, not a probability —
measured Brier 0.1642, ECE 0.0905, overconfident above 0.8 (`docs/benchmarks/
calibration.md`). Review-queue thresholds and the CRM gate's 0.6/0.85 cutoffs would all
get sharper with calibrated scores. The training data already exists by design: every
field observation stores its component vector, and review/reconciliation outcomes label it.

**Acceptance criteria:**
- A calibration layer (isotonic/Platt over the rules-v1 components, or a small learned
  model) trained on persisted observation outcomes.
- ECE < 0.0905 and Brier < 0.1642 on a held-out split of the same evaluation harness
  (`make calibration`), with the 0.8+ overconfidence buckets specifically improved.
- Served side-by-side with rules-v1 (`formula_version` already exists per evaluation);
  gate/review thresholds configurable per formula version. Rules-v1 remains the
  documented fallback.

### 4. Redis-backed rate limiter and circuit breaker

**Why:** the current breaker (`relayiq/providers/registry.py::CircuitBreaker`) and
sliding-window limiter are per-process — a documented limitation. With N workers, each
process discovers a provider outage independently and the fleet collectively overruns
vendor rate limits. This blocks any multi-worker production deployment.

**Acceptance criteria:**
- Breaker state and rate-limit windows shared via Redis (atomic Lua or token bucket);
  behavior unchanged in single-process mode.
- Integration test: two worker processes, one provider forced into outage — both stop
  calling it within the breaker threshold, combined call rate respects the provider limit.
- Failure mode documented: Redis unavailable → fall back to per-process limits (fail
  open on limiting, fail closed on nothing).

## P2 — surface area

### 5. Salesforce adapter

**Why:** currently **designed only**. HubSpot-first was the right MVP call, but RevOps
buyers split across the two; the CRM port is where the "interface parallel to HubSpot"
design gets validated or falsified.

**Acceptance criteria:**
- Adapter implements the same CRM interface (upsert, read-back, secondary-property
  equivalent via custom fields) with fixture tests at parity with HubSpot's suite.
- CRM gate outcomes map to Salesforce semantics with documented differences.
- e2e scenario 12 (low confidence never overwrites CRM) runs against the Salesforce
  adapter in simulator/fixture mode; live verification tracked like item 1.

### 6. Playwright UI suite expansion

**Why:** the dashboard has 6 Playwright specs (`apps/dashboard/e2e/critical-flows.spec.ts`)
covering login, overview, lineage, review accept/reverse, analytics, and read-only roles.
The review and CRM surfaces are the product's trust story — regressions there are
customer-visible even when the API suite stays green.

**Acceptance criteria:**
- Coverage added for: new-enrichment form validation states, review `correct_value` and
  `select_observation` paths, reverse-confirm dialog cancel path, CRM before/after
  expansion and simulator tab, lineage rendering of rejected observations and fallbacks.
- Suite runs in CI against the seeded compose stack; flake rate < 1% over 20 CI runs.

## P3 — architecture triggers (do when the trigger fires, not before)

### 7. Temporal migration triggers

**Why:** ADR-001 chose Celery and made durability the application's job — an explicit,
revisitable decision with documented gaps (no durable timers for `mark_refresh`, queued
tasks lost on broker restart, mid-step crash can re-spend within bounded retries). The
roadmap item is not "migrate to Temporal"; it is instrumenting the triggers so the
migration decision is data-driven.

**Acceptance criteria:**
- ADR-001's revisit conditions monitored: alert/report on (a) double-spend ledger
  incidents traced to mid-step crashes, (b) jobs stranded in `queued` after broker
  restarts, (c) any feature requiring human-in-the-loop waits or durable timers.
- A stopgap reconciliation sweep for stuck `queued` jobs (the ADR names this gap).
- A written go/no-go: if any trigger fires at meaningful frequency for a quarter, spike a
  Temporal port of the orchestrator's step model; otherwise stay on Celery.

### 8. Contextual bandit routing (needs >2 providers)

**Why honestly last:** the benchmark showed dynamic routing **losing** to a well-tuned
static policy at 2-provider scale — 5.94 credits per usable lead vs static's 4.07 —
because warmup exploration wasn't recouped (`docs/benchmarks/results.md`). With two
providers there is almost nothing to learn that the static priors don't encode. Bandit
routing becomes worth revisiting only when 3+ providers with overlapping field coverage
make the explore/exploit tradeoff real.

**Acceptance criteria:**
- Precondition: ≥3 provider adapters (simulated is fine — add a third personality)
  with overlapping coverage on at least 4 fields.
- A contextual bandit (e.g. Thompson sampling over per-field observed precision, warm-
  started from `FIELD_QUALITY_PRIORS`) implemented as a new routing strategy behind the
  existing policy mechanism.
- Benchmark gate: it must **beat static routing on cost per true usable lead** in the
  standard seeded benchmark, including its warmup spend, before becoming a default
  anywhere. If it loses again, the result gets published like last time.

---

*Not on this roadmap but tracked elsewhere: OAuth/SSO replacing the dev JWT login,
external security review, and compliance work — see `SECURITY.md` and
`docs/interview-guide.md` §12.*
