# Load test results (measured 2026-07-13)

**Environment: development laptop (macOS, Apple Silicon), API + Postgres 16 (Docker) +
Redis 7 (Docker) all on one machine, single uvicorn process. These numbers characterize
the implementation's behavior at small scale — they are NOT production capacity claims.**

Tool: Locust (`tests/load/locustfile.py`), 25 concurrent users, spawn rate 5/s, 60s run,
mixed read/write traffic. Reproduce with `make load-test` (raw CSVs in `docs/benchmarks/load_*.csv`).

## Aggregate (measured)

| Metric | Value |
|---|---|
| Requests | 2,061 |
| Failures | **0** |
| Sustained throughput | 35.4 req/s |
| p50 / p95 / p99 latency | 32 ms / 580 ms / 970 ms |

## By endpoint (measured, ms)

| Endpoint | n | p50 | p95 | p99 |
|---|---|---|---|---|
| POST /v1/enrichment/execute (new identity — full pipeline incl. simulated provider calls) | 330 | 120 | 880 | 1500 |
| POST /v1/enrichment/execute (repeat pool — mostly canonical-store hits) | 240 | 89 | 790 | 1200 |
| POST /v1/enrichment/execute (idempotent replay) | 97 | **12** | 69 | 150 |
| GET /v1/metrics/overview | 520 | 35 | 95 | 190 |
| GET /v1/enrichment/jobs | 395 | 17 | 61 | 120 |
| GET /v1/review/queue | 236 | 8 | 44 | 89 |
| GET /v1/contacts | 231 | 14 | 65 | 110 |

## Reading these numbers honestly

- Idempotent replays are ~10× faster than fresh enrichments (12ms vs 120ms p50) and spend
  zero credits — the durable-idempotency design doing its job under load.
- Full-pipeline latency includes ~9 persisted decision records per job (workflow steps,
  routing, observations, reconciliation, confidence, ledger) in synchronous mode; the p95
  tail is dominated by jobs that trigger the stale cross-check (a second provider call).
- Provider "latency" is simulated and *reported*, not slept, so these figures measure the
  control plane itself, not network waits.
- Single-process sync mode was tested deliberately (worst case). Production deployments
  run enrichment through Celery workers (`mode: "async"`), which moves the pipeline off
  the request path entirely.
