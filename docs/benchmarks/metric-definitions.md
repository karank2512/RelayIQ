# Metric definitions

Every number RelayIQ reports is computed from persisted rows by the code cited below —
nothing is hardcoded or estimated. This page is the normative definition; the SQL/Python
in `relayiq/services/ledger.py`, `relayiq/services/quality.py`,
`relayiq/services/review.py`, `relayiq/benchmark/runner.py`, and
`relayiq/benchmark/calibration.py` implements it.

Notation: jobs `J`, ledger entries `L`, observations `O`, reconciliation decisions
`R`, review tasks/decisions `T`/`D` — all tenant-scoped (and optionally
campaign-scoped) in the production metrics; the benchmark computes the same
quantities in memory against synthetic ground truth.

## Cost metrics (`services/ledger.py::cost_per`, `cost_summary`)

Let `attempted = { j ∈ J : j.status ≠ "received" }` (jobs that reached the pipeline)
and `C = Σ_{j ∈ attempted} j.actual_cost_credits`.

| Metric | Formula |
|---|---|
| **cost per attempted record** | `C / |attempted|` |
| **cost per accepted record** | `C / |{ j : j.result_summary.accepted }|` |
| **cost per complete record** | `C / |{ j : j.result_summary.all_requested_fields_filled }|` — `filled ≥ |requested_fields|` where `filled = accepted fields + cache-served fields` (orchestrator `finalize` step) |
| **cost per usable lead** | `C / |{ j : j.result_summary.usable_lead }|` (usable-lead definition below) |
| **total spend** | `Σ L.actual_cost_credits` |
| **redundant cost avoided** | `Σ L.avoided_cost_credits` — written on cache-hit entries with the avoided cost measured from the live cheapest provider price (`orchestrator.py`), so avoidance is *measured*, not estimated |
| **redundant spend** | `Σ L.actual_cost_credits where L.was_redundant` |
| **spend on stale data** | `Σ L.actual_cost_credits where L.spent_on_stale` (source age > 180 days at purchase time) |
| **spend on later-rejected records** | `Σ L.actual_cost_credits where L.record_rejected_later` |

Denominators of zero yield `null`, never zero-division or a fabricated 0.

The benchmark's cost-per variants (`benchmark/runner.py::StrategyResult.summary`)
are the same shape over strategy-local counters:
`cost_per_claimed_usable = cost_credits / claimed_usable_leads` and
`cost_per_true_usable = cost_credits / true_usable_leads`. Measured values for the
seeded run are in `docs/benchmarks/results.md` (e.g. naive 13.242 → static routing
4.065 → full pipeline 4.652 credits per true usable lead).

## Usable-lead definition

**Production** (`services/quality.py::evaluate_usable_lead`; every criterion is
configurable via `relayiq/config.py` `usable_lead_*` settings so cost-per-usable-lead
can be re-derived under different definitions). A contact is a usable lead iff **all**
hold:

1. matched company (`account_id` or `company_domain`) — `usable_lead_require_company`
2. valid company domain (`is_valid_domain`) — `usable_lead_require_valid_domain`
3. contact name present (`full_name` or `last_name`) — `usable_lead_require_contact_name`
4. accepted, non-stale job title (canonical `job_title` in state fresh/aging) **or**
   accepted seniority — `usable_lead_require_title_or_seniority`
5. entity confidence ≥ `usable_lead_min_confidence` (default 0.6)
6. zero pending review tasks for the contact
7. not suppressed by policy
8. eligible for CRM sync (campaign `crm_write_enabled`)

Failures are returned as a list and stamped into
`job.result_summary.usable_lead_failures`.

**Benchmark** (`benchmark/runner.py::_lead_quality`) adds ground truth:

- `claimed_usable` — valid company domain ∧ contact name ∧ (delivered job_title ∨
  seniority) ∧ not pre-filtered (suppression / invalid domain / campaign-country /
  low-value / missing email). This mirrors what a strategy *believes* it delivered.
- `true_usable` — claimed **and** the delivered `job_title` or `seniority` matches
  the synthetic world's known truth (`values_equivalent`). Filling a CRM with wrong
  values does not count.

Benchmark fill/precision/usable metrics are computed over **eligible** records only
(non-pre-filtered), so strategies share a denominator; spend on ineligible records
still counts in cost (`_score_delivery`). The full pipeline's review-queue records
are conservatively **excluded** from its usable-lead count (human review is not
simulated).

## Quality metrics (`services/quality.py::quality_summary`)

| Metric | Formula |
|---|---|
| **fill rate** | `Σ result_summary.fields_filled / Σ |requested_fields|` over jobs with `pre_decision = "enrich"`. Benchmark analogue: `fields_filled / fields_requested` over eligible records. |
| **conflict rate** | `(R[require_review] + R[accept_with_warning] + R[retain_crm]) / Σ R` — the share of reconciliations where providers materially disagreed |
| **staleness share** | `(canonical values in state stale + expired) / all canonical values` |
| **field precision vs truth** (benchmark only) | `fields_correct / fields_filled`, correctness by `values_equivalent(field, delivered, truth)`. Measured: 0.682 naive → 0.773 static routing → 0.7836 full pipeline. Production has no ground truth; the observational proxy is selection share below. |
| **provider field quality** (`provider_field_quality`) | per provider × field: `selected_share = selected/total`, `rejected_share = rejected/total` over `field_observations` |
| **CRM sync failure rate** | `sync attempts with status failed / all sync attempts` |

**Redundant-call rate** (benchmark framing): the duplicate-submission share of the
stream that a strategy re-buys. In the seeded run (`docs/benchmarks/results.json`)
the stream is 495 submissions over 431 unique contacts (15% duplicate deliveries);
naive makes 990 provider calls vs 862 with caching — the ledger's `was_redundant` /
`avoided_cost_credits` fields carry the production equivalent per entry.

## Review metrics (`services/review.py::queue_metrics`)

Over resolved tasks (`accepted | overridden | rejected`):

| Metric | Formula |
|---|---|
| **acceptance rate** | `accepted / resolved` — reviewer confirmed the suggested value |
| **override rate** | `overridden / resolved` — reviewer picked a different observation or typed a correction |
| **reversal rate** | `reverse decisions / resolved` — how often resolved decisions were later undone (`D.action = "reverse"`) |
| **avg review seconds** | mean of `resolved_at − first_opened_at` |

Review acceptance is also fed back into per-provider spend value:
`ledger.mark_acceptance` flags which providers' entries produced accepted values
(`result_accepted`).

## Latency metrics

- **Provider p50/p95/p99** (`services/provider_exec.py`): per 5-minute
  `provider_health_windows` bucket, a bounded reservoir (≤500 samples) is kept and
  percentiles computed by nearest-rank: `pct(p) = sorted[min(n−1, ⌊p·n⌋)]`.
  `provider_stats` merges reservoirs across a window (default 24 h) the same way.
  Simulator latency is *reported*, not slept (ADR-009) — these measure the modeled
  distribution plus control-plane accounting, not network waits.
- **HTTP p50/p95** (`observability/metrics.py::HTTP_LATENCY`): Prometheus histogram;
  p95 is `histogram_quantile(0.95, rate(relayiq_http_request_seconds_bucket[5m]))`,
  i.e. bucket-interpolated, not exact. The measured load-test values (p50 32 ms /
  p95 580 ms overall; idempotent replays p50 12 ms) come from Locust's own
  measurement on a dev laptop (`docs/benchmarks/load-test-results.md`).
- **Job duration**: `relayiq_enrichment_job_seconds` histogram, labeled by terminal
  status.

## Calibration metrics (`benchmark/calibration.py::evaluate`)

Over `N` (confidence `c_i`, correct `o_i ∈ {0,1}`) pairs — one per pipeline-accepted
field, correctness vs synthetic truth:

- **Brier score** `= (1/N) Σ (c_i − o_i)²` — lower is better; 0.25 is the score of a
  constant 0.5 forecast. **Measured: 0.1642.**
- **Expected Calibration Error** `= Σ_b (n_b/N) · |acc_b − conf_b|` over 10
  equal-width confidence buckets, where `acc_b` is the accuracy and `conf_b` the mean
  confidence within bucket `b`. **Measured: 0.0905**, with the miscalibration
  concentrated **above 0.8** (mean confidence 0.87–0.92 vs accuracy ~0.81–0.82 in the
  top buckets — see `docs/benchmarks/calibration.md`).

Interpretation rule used throughout the docs: rules-v1 confidence is a **ranking
signal, not a probability**. It orders records well enough to gate review and sync,
but a "0.9" is not a 90% chance of being right on this distribution.

## p95 vs averages — reporting rule

Wherever this project reports latency it reports percentiles (p50/p95/p99), never
means; wherever it reports cost-per-X it reports the denominator definition alongside
the number. Numbers cited in docs are either **measured** (benchmark, calibration,
load test — with generation timestamps in the artifacts) or explicitly marked as
simulated parameters.
