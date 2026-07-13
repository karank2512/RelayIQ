# RelayIQ — Final Build Report (2026-07-13)

## 1. Implementation summary

RelayIQ is a complete, runnable enrichment control plane: a FastAPI + Celery backend
(32-table PostgreSQL schema, Redis cache, deterministic provider simulators), a React/Vite
operations dashboard (15 routes), a measured benchmark and calibration suite, CI, container
images, Terraform templates, and a full documentation set (13 ADRs, threat model, pilot kit).
Everything demanded by the spec's MVP + portfolio phases is implemented; two Phase-3 items
(Salesforce adapter, learned confidence model) are designed-only by scope decision.

## 2. Architecture summary

- **API** (`apps/api/relayiq`): versioned REST (auth, enrichment sidecar, entities, review,
  admin, metrics, CRM, HMAC webhooks, health). JWT auth (dev OAuth substitute), roles
  re-verified against the DB per request, tenant scoping on every query.
- **Orchestrator** (`engines/orchestrator.py`): pre-decision → routing → cache → atomic
  budget reservation → provider calls with bounded retries/circuit breakers/per-field
  fallback + staleness-triggered cross-checks → observation persistence (never overwrite) →
  reconciliation with prose reasoning → rules-v1 confidence → auto-accept or review →
  per-field CRM gate → sync → ledger/lineage/audit. Runs sync (API) or async (Celery).
- **Providers**: adapter SDK with two deterministic simulator personalities reading a
  synthetic world with known ground truth (RFC-2606 `.test` domains, invented personas).
- **State**: PostgreSQL is the source of truth; Redis carries the field cache
  (tenant+schema-versioned keys, SWR, stampede locks) and Celery broker.
- **Observability**: Prometheus metrics (bounded cardinality), OTel tracing, structured
  JSON logs with redaction, correlation IDs, Grafana dashboard (13 panels, validated).

## 3. Key file tree

```
apps/api/relayiq/{engines,services,providers,canonical,api,workers,seed,benchmark,observability}
apps/api/{alembic,tests/{unit,integration,e2e},Dockerfile}
apps/dashboard/{src/{pages,components,lib},e2e,Dockerfile,nginx.conf}
infrastructure/{terraform,grafana,prometheus}   .github/workflows/{ci,security}.yml
docs/{adr(13),architecture(6),security,benchmarks,pilot(8),portfolio,screenshots(10)}
tests/load/locustfile.py   docker-compose.yml   Makefile
```

## 4. Feature status

| Feature | Status |
|---|---|
| Canonical model, observations, migrations (32 tables) | **Complete** |
| Provider SDK + 2 simulators (all knobs, deterministic) | **Complete (simulation by design)** |
| Pre-enrichment decision engine (8 outcomes, 12 checks) | **Complete** |
| Field-level routing (YAML policies, 4 strategies, fallback, cross-check) | **Complete** |
| Redis cache (TTL/negative/SWR/stampede/tenant+schema keys, measured avoidance) | **Complete** |
| Idempotency (requests/webhooks/sync/review; DB-unique claims) | **Complete** |
| Cost ledger + all cost metrics incl. cost-per-usable-lead | **Complete** |
| Conflict reconciliation (6 outcomes, human-readable reasoning) | **Complete** |
| Rules-based confidence + measured calibration report | **Complete** (miscalibration documented) |
| Review queue (accept/select/correct/reject/defer/note/reverse, audited) | **Complete** |
| Clay-compatible sidecar endpoints | **Complete using simulation** (live Clay NOT tested) |
| HubSpot sync adapter | **Complete using fixtures** (live NOT verified) |
| CRM sync gate (6 outcomes, per-field reasons) + CRM simulator | **Complete** |
| Staleness policies (global/tenant, affects cache/routing/confidence/sync/review) | **Complete** |
| Provider health windows + circuit breaker | **Complete** (per-process; Redis-backed is roadmap) |
| Source lineage (full chain, API + UI explorer) | **Complete** |
| Ops dashboard (15 routes, role-aware) | **Complete** |
| HMAC webhooks (constant-time, replay window, rotation, dedup) | **Complete** |
| OTel + Prometheus + Grafana + alert rules | **Complete** |
| AuthN/Z (4 roles; dev JWT substitute documented) | **Complete** |
| Dynamic provider selection + benchmark comparison | **Complete** (honestly loses at 2-provider scale) |
| Budgets (hard/soft/daily/lifetime/per-record, concurrency-safe, degradation) | **Complete** |
| Multi-tenancy (DB/Redis/policy isolation, tested) | **Complete** |
| Salesforce adapter | **Designed, not implemented** (roadmap) |
| Privacy/permitted-use policy engine | **Complete** (technical control, not legal compliance) |
| Learned confidence model | **Designed, not implemented** (must beat ECE 0.0905) |

## 5. Test results (all actually executed)

- **Unit: 259 passed** (normalization, HMAC, SSRF, staleness, confidence, reconciliation,
  routing, budget, idempotency, cache, ledger, decision engine, CRM gate, simulators, worldgen)
- **Integration: 32 passed** vs real Postgres+Redis (auth/role matrix, cross-tenant isolation,
  cache-zero-spend, ledger persistence, webhook security paths, HubSpot fixtures, and 5
  concurrency races: identical requests → 1 job; parallel reserves never breach hard budgets;
  single stampede-lock winner; simultaneous reviewer actions; duplicate CRM syncs)
- **E2E: all 12 required scenarios passed** (`tests/e2e/test_scenarios.py`)
- **Playwright: 5 passed, 1 conditionally skipped** (reviewer flow passes when demo review
  tasks exist; it consumed them on its verified pass)
- **Load (dev laptop): 2,061 requests, 0 failures**, 35.4 req/s, p50 32ms / p95 580ms / p99
  970ms; idempotent replays p50 12ms
- **Security tests**: 50 webhook-HMAC tests (incl. constant-time-path assertion), 47 SSRF
  tests, role/tenant enforcement in integration
- Lint (ruff) and frontend typecheck/lint clean; CI configured for all of the above

## 6. Benchmark results (seeded synthetic providers, real control-plane code)

| Strategy | Cost | Precision | True usable | Cost/usable |
|---|---|---|---|---|
| Naive | 2,886.8 | 0.682 | 218 | 13.24 |
| + cache | 2,514.6 | 0.682 | 218 | 11.54 |
| + filters | 1,318.6 | 0.682 | 218 | 6.05 |
| + static field routing | 987.8 | 0.773 | 243 | **4.07** |
| Full RelayIQ | 1,097.8 | **0.784** | 236 | 4.65 |
| Dynamic routing | 1,329.8 | 0.728 | 224 | 5.94 |

Honest findings: field-level routing improved cost AND quality simultaneously; the full
pipeline pays +11% over static routing for +1.1pp precision and review triage; dynamic
routing lost to a tuned static policy at 2-provider scale (warmup cost unrecouped).
Calibration: Brier 0.164, ECE 0.091 — the confidence score is a ranking signal, not a
probability, and the docs say so.

## 7. Security review

Implemented: HMAC with `hmac.compare_digest` + replay windows + rotation + delivery dedup;
SSRF guard with DNS-rebinding stance + send-time re-validation; JWT with DB-verified roles;
tenant scoping; DB-atomic budget reservation and idempotency claims; bounded retries +
circuit breakers; log redaction; pip-audit/gitleaks/trivy in CI; non-root containers.
Remaining risks (documented in SECURITY.md/threat model): dev JWT substitute, per-process
rate limiting, one shared webhook secret across tenants, no external security review.

## 8. Run locally

```bash
cp .env.example .env && docker compose up --build
# dashboard :5173 (operator@demo.relayiq.test / relayiq-demo-password), API :8000/docs,
# Grafana :3000, Prometheus :9090
# Host-based dev: make setup && make dev-deps && make migrate && make seed && make api
```

## 9. Deployment

`docs/deployment.md` + `infrastructure/terraform/` (Fly.io templates via flyctl — nothing
applied; managed Postgres/Upstash Redis suggestions; required env vars tabled; ~$0–25/mo).
Frontend deploys to any static host with `/v1` proxying (nginx config provided).

## 10. Known limitations

Simulated provider economics; miscalibrated confidence (measured, documented); live
Clay/HubSpot unverified; Salesforce and learned model designed-only; single-node
limiter/breaker; laptop load numbers; webhook tenant resolution by slug under one secret.

## 11. Two-minute demo

Follow `docs/pilot/demo-script.md`: login → Overview (cost/usable-lead from the ledger) →
submit enrichment → lineage explorer (routing factors, both providers' values, reconciliation
prose, confidence components) → review accept → reverse (history intact) → CRM tab
(before/after + gate reasons) → replay a signed webhook (duplicate:true, zero spend) →
Analytics. Talk track: decisions-as-data, spend-as-ledger, the honest benchmark. Don't
overclaim: say "simulated providers, measured control plane."

## 12. Recommended next steps (by hiring signal × product value)

1. Live HubSpot sandbox verification (turns "fixture-tested" into "live-verified")
2. Learned confidence model beating ECE 0.0905 (the calibration report is the perfect setup)
3. Third provider simulator → re-run dynamic routing benchmark (give the bandit room to win)
4. Redis-backed rate limiter + circuit breaker (multi-node correctness)
5. Clay live test through the HTTP-API column
