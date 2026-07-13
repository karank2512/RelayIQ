# Observability

Three signals, all wired in code (not aspirational): Prometheus metrics
(`apps/api/relayiq/observability/metrics.py`, served at `/metrics` when
`RELAYIQ_METRICS_ENABLED`), OpenTelemetry traces
(`apps/api/relayiq/observability/tracing.py`), and structured JSON logs with
redaction (`apps/api/relayiq/logging_setup.py`). The local compose stack ships
Prometheus (:9090) and Grafana (:3000); see `docs/deployment.md`.

## Instrumented metrics (complete list from `observability/metrics.py`)

All label sets are bounded-cardinality by construction — **no tenant IDs, entity IDs,
or free-form strings as label values** (per-tenant analytics come from the
tenant-scoped Postgres endpoints instead; see `docs/architecture/tenancy.md`).

| Metric | Type | Labels | Incremented in |
|---|---|---|---|
| `relayiq_http_requests_total` | counter | `method, route, status` | middleware (`main.py`) |
| `relayiq_http_request_seconds` | histogram (10ms–10s buckets) | `method, route` | middleware (`main.py`) |
| `relayiq_cache_ops_total` | counter | `entity_type, field, status` (hit/stale_hit/negative_hit/miss/corrupt_miss) | `services/cache.py` |
| `relayiq_provider_calls_total` | counter | `provider, outcome` | `services/provider_exec.py` |
| `relayiq_provider_latency_ms` | histogram (25–6400ms buckets; *reported* simulator latency) | `provider` | `services/provider_exec.py` |
| `relayiq_provider_cost_credits_total` | counter | `provider` | `services/ledger.py` |
| `relayiq_provider_retries_total` | counter | `provider` | `services/provider_exec.py` |
| `relayiq_provider_circuit_open` | gauge (0/1) | `provider` | `services/provider_exec.py` |
| `relayiq_enrichment_jobs_total` | counter | `status` (terminal) | `engines/orchestrator.py` |
| `relayiq_enrichment_job_seconds` | histogram (50ms–30s buckets) | `status` | `engines/orchestrator.py` |
| `relayiq_pre_decisions_total` | counter | `decision` | `engines/orchestrator.py` |
| `relayiq_routing_decisions_total` | counter | `provider, strategy` | `engines/orchestrator.py` |
| `relayiq_reconciliations_total` | counter | `outcome` | `engines/orchestrator.py` |
| `relayiq_review_actions_total` | counter | `action` | `services/review.py` |
| `relayiq_review_queue_depth` | gauge | — | `services/review.py` |
| `relayiq_crm_syncs_total` | counter | `system, status` | `services/crm_sync.py` |
| `relayiq_crm_gate_total` | counter | `outcome` | `services/crm_gate.py` |
| `relayiq_webhooks_total` | counter | `source, result` | `api/routers/webhooks.py` |
| `relayiq_budget_blocks_total` | counter | `kind` (hard/per_record_max) | `services/budget.py` |

## Tracing and correlation IDs

- **Correlation ID** — the HTTP middleware (`main.py`) takes `X-Correlation-Id` or
  mints one, stores it in a `ContextVar` (`logging_setup.correlation_id_var`), stamps
  it on every log line via the `add_correlation` processor, and returns it in the
  response header. The verified tenant is likewise injected into log context
  (`tenant_id_var`, set in `api/deps.py`).
- **OpenTelemetry** — `configure_tracing()` sets a `TracerProvider`
  (service name `relayiq-api`); spans export via OTLP-HTTP when
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set, otherwise stay in-process (still useful for
  trace-ID correlation). FastAPI is auto-instrumented
  (`FastAPIInstrumentor`, excluding healthz/readyz/metrics).
- **Per-step spans** — the orchestrator opens a span per pipeline step
  (`enrich.pre_decision`, `enrich.cache_check`, `enrich.routing`,
  `enrich.budget_reserve`, `enrich.provider_calls`, `enrich.reconciliation`,
  `enrich.confidence`, `enrich.crm_sync`, `enrich.finalize`).
- **Trace IDs persisted on rows** — `trace_id` columns on `enrichment_jobs`,
  `field_observations`, `provider_requests`, `cost_ledger_entries`,
  `routing_decisions` (via job), `crm_sync_attempts`, and `audit_events`, so a trace
  in the backend links directly to the decisions and spend it produced.

## Log redaction

`logging_setup.py` renders structlog → JSON with two safety processors:

- `redact_processor` replaces values under sensitive keys (`password`,
  `password_hash`, `token`, `access_token`, `refresh_token`, `authorization`,
  `secret`, `api_key`, `apikey`, `signature`, `hubspot_access_token`, `jwt`) with
  `[REDACTED]`, and masks email-like values under `*email*` keys
  (`jo***@example.com`).
- Sensitive call sites are additionally careful: webhook rejections log only the
  failure class; worker callback failures log the exception type, not the URL/body.

Known residuals (threat model §10): redaction is key-based (nested payloads under
innocuous keys escape it), and Redis cache key *names* embed emails/domains
(ADR-003).

## Alert rules (8 documented rules)

These are the documented starting rules with proposed thresholds — thresholds are
judgment defaults for a small deployment, **not** measured SLOs. All expressions use
the real metric names above.

```yaml
groups:
  - name: relayiq
    rules:
      # 1. Webhook forgery / misconfiguration spike — someone is sending bad signatures.
      - alert: WebhookSignatureFailures
        expr: >
          sum(rate(relayiq_webhooks_total{result=~"invalid_signature|missing_signature|malformed_header|stale_timestamp"}[5m])) > 0.1
        for: 10m
        labels: {severity: warning}
        annotations:
          summary: "Webhook signature/replay rejections sustained (>0.1/s for 10m) — rotation gone wrong or active probing"

      # 2. Provider circuit breaker open — a provider is effectively down.
      - alert: ProviderCircuitOpen
        expr: max by (provider) (relayiq_provider_circuit_open) > 0
        for: 5m
        labels: {severity: warning}
        annotations:
          summary: "Circuit breaker open for provider {{ $labels.provider }} for 5m — traffic is falling back or failing"

      # 3. Provider error ratio — degradation below breaker threshold still wastes routing.
      - alert: ProviderErrorRatioHigh
        expr: >
          sum by (provider) (rate(relayiq_provider_calls_total{outcome!="success"}[10m]))
          / sum by (provider) (rate(relayiq_provider_calls_total[10m])) > 0.25
        for: 10m
        labels: {severity: warning}
        annotations:
          summary: "Provider {{ $labels.provider }} failing >25% of calls — routing health_penalty is halving its score; check upstream"

      # 4. API 5xx rate — the middleware converts unhandled errors to 500s.
      - alert: Http5xxErrors
        expr: >
          sum(rate(relayiq_http_requests_total{status=~"5.."}[5m]))
          / sum(rate(relayiq_http_requests_total[5m])) > 0.01
        for: 5m
        labels: {severity: critical}
        annotations:
          summary: ">1% of HTTP requests are 5xx — check logs by correlation_id"

      # 5. API latency — p95 above 1s (dev-laptop measured baseline was 580ms p95 under load).
      - alert: HttpP95LatencyHigh
        expr: >
          histogram_quantile(0.95, sum by (le) (rate(relayiq_http_request_seconds_bucket[5m]))) > 1
        for: 10m
        labels: {severity: warning}
        annotations:
          summary: "HTTP p95 above 1s for 10m — compare per-route with sum by (route, le)"

      # 6. Budget blocks — spend is being refused; either budgets are too tight or something is spending hard.
      - alert: BudgetBlocksOccurring
        expr: sum by (kind) (increase(relayiq_budget_blocks_total[15m])) > 10
        for: 0m
        labels: {severity: warning}
        annotations:
          summary: ">10 budget blocks ({{ $labels.kind }}) in 15m — check the cost ledger for the spending source"

      # 7. Review queue backlog — humans are the bottleneck; usable-lead output stalls.
      - alert: ReviewQueueBacklog
        expr: relayiq_review_queue_depth > 50
        for: 30m
        labels: {severity: warning}
        annotations:
          summary: "Review queue above 50 pending tasks for 30m — leads are blocked from usable/sync status"

      # 8. Job failure ratio — orchestrator or worker trouble (jobs park as failed after bounded retries).
      - alert: EnrichmentJobFailures
        expr: >
          sum(rate(relayiq_enrichment_jobs_total{status="failed"}[15m]))
          / sum(rate(relayiq_enrichment_jobs_total[15m])) > 0.05
        for: 15m
        labels: {severity: critical}
        annotations:
          summary: ">5% of enrichment jobs terminating as failed — inspect workflow_steps for the failing step"
```

Useful companion queries (dashboards, not alerts):

- Cache effectiveness:
  `sum(rate(relayiq_cache_ops_total{status="hit"}[30m])) / sum(rate(relayiq_cache_ops_total{status=~"hit|miss|stale_hit|negative_hit"}[30m]))`
- Spend rate by provider: `sum by (provider) (rate(relayiq_provider_cost_credits_total[1h]))`
- Reconciliation conflict share:
  `sum(rate(relayiq_reconciliations_total{outcome=~"require_review|accept_with_warning|retain_crm"}[1h])) / sum(rate(relayiq_reconciliations_total[1h]))`
- Pre-decision mix (how much work the filters kill before spend):
  `sum by (decision) (rate(relayiq_pre_decisions_total[1h]))`
- CRM gate outcomes: `sum by (outcome) (rate(relayiq_crm_gate_total[1h]))`

## Caveats

- `relayiq_provider_latency_ms` observes the simulators' *reported* latency
  (ADR-009) — it characterizes the modeled distribution, not real network waits.
- `relayiq_review_queue_depth` is set on review actions (`services/review.py`) and
  currently reflects the global pending count, refreshed when reviewers act — it can
  lag between actions.
- Counters/gauges are per-process; with multiple API/worker processes, aggregate in
  PromQL (`sum(...)`) and remember breaker gauges are per-process too
  (SECURITY.md §3).
