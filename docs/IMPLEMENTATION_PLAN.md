# RelayIQ — Implementation Plan & Build State

> Working checklist for the build. Updated as milestones complete.
> Status legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[s]` simulated · `[d]` designed-only

## Phase 0 — Assessment (2026-07-11)

- Repository was **empty** at start. Greenfield monorepo initialized on branch `feature/relayiq-mvp`.
- Environment: macOS, Python 3.13.2, Node 25.6, Docker 28 (Compose v2), no uv/poetry → plain venv + pip-tools style pinned requirements.
- No real provider / Clay / HubSpot credentials available → **all external integrations are simulated behind adapter interfaces** (documented per integration).

### Key assumptions (recorded per protocol)

1. Python 3.13 satisfies the "3.12 or later" requirement.
2. Single Python project at `apps/api` (FastAPI + Celery worker + provider SDK as subpackages) instead of separate `packages/*` pip projects — see ADR-013. Boundaries kept via subpackage layout (`relayiq/providers`, `relayiq/canonical`).
3. Sync SQLAlchemy 2.0 + psycopg3; FastAPI runs sync endpoints in threadpool. Simpler Celery sharing; adequate for portfolio-scale load.
4. Dev auth = JWT (HS256) + seeded bcrypt users with roles; documented as the OAuth substitute.
5. "Credits" are the canonical cost unit; USD conversion is provider-config metadata.
6. CRM default = built-in simulator (`crm_sim_records` table); HubSpot adapter implemented against fixtures, live sync **not verified**.

## Phase 1 — MVP

- [x] Repo scaffold, Makefile, docker-compose, .env.example
- [x] DB schema (all ~28 tables) + Alembic migrations
- [x] Canonical normalization (names, domains, titles, seniority, industry, employee ranges)
- [x] Provider SDK interface + simulators Alpha & Beta (deterministic, configurable)
- [x] Pre-enrichment decision engine (reject/skip/cached/enrich/review/budget-block/policy-block)
- [x] Field-level routing engine + YAML policy format
- [x] Redis cache layer (tenant/schema-aware keys, TTL, negative cache, stampede lock, SWR, metrics)
- [x] Idempotency service (requests, webhooks, sync jobs, review actions)
- [x] Cost ledger + derived metrics
- [x] Conflict reconciliation engine w/ human-readable reasoning
- [x] Rules-based confidence model (field/entity/sync levels) + calibration eval
- [x] Manual review queue (API + UI) with reversible, audited actions

## Phase 2 — Portfolio-ready

- [x] Clay-compatible sidecar endpoints (decide/execute/batch/jobs/entities/webhooks) `[s]` for live Clay
- [x] HubSpot adapter (fixtures + mock mode; live not verified) `[s]`
- [x] CRM sync gate
- [x] Staleness policies (configurable per field)
- [x] Provider health & circuit breaker
- [x] Source lineage API + explorer UI
- [x] Ops dashboard (overview/providers/campaigns/review/lineage)
- [x] HMAC webhook validation (constant-time, replay window, delivery dedup, rotation)
- [x] OpenTelemetry + Prometheus metrics + Grafana dashboards
- [x] AuthN/AuthZ (admin/operator/reviewer/analyst)

## Phase 3 — Advanced

- [x] Dynamic provider selection (transparent scoring vs static baselines) — benchmarked
- [x] Campaign budgets (hard/soft/daily/lifetime, concurrency-safe, degradation modes)
- [x] Multi-tenancy (DB scoping, Redis key prefixes, policy isolation, tests)
- [d] Salesforce adapter — interface + design + backlog (ADR)
- [x] Privacy / permitted-use policy engine (technical control; not legal compliance)
- [d] Learned confidence model — design + data requirements documented; rules-based shipped

## Cross-cutting

- [x] Synthetic data generator ("world truth" + per-provider distortions)
- [x] Benchmark harness (naive vs cache vs filter+cache vs static routing vs full RelayIQ)
- [x] Tests: unit, integration, e2e scenarios, concurrency; load test (Locust)
- [x] CI (GitHub Actions), dependency & secret scanning
- [x] Terraform (Fly.io-style small deployment), deployment docs
- [x] Docs: README, ADRs, threat model, SECURITY.md, benchmarks, calibration, pilot kit, interview guide, resume artifacts

## Security-sensitive areas (tracked)

- Webhook HMAC verification (`relayiq/services/webhook_security.py`)
- Idempotency + budget race safety (DB-level atomic claims)
- Callback URL SSRF validation (`relayiq/services/ssrf.py`)
- JWT handling / role checks (`relayiq/api/deps.py`)
- Log redaction (`relayiq/logging_setup.py`)
- Raw provider payload retention (ADR-012)
