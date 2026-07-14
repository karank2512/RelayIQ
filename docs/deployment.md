> **Before any production deploy, work through [production-checklist.md](production-checklist.md)** —
> the app fail-fast validates its configuration when `RELAYIQ_ENV=production`.

# RelayIQ — Deployment Guide

RelayIQ ships as one backend image (FastAPI API + Celery worker, same image,
different command) plus a static dashboard image, with Postgres and Redis as
external state. Providers in this build are **simulators** — no real
third-party enrichment API is called anywhere.

## 1. Local (docker compose)

Full stack — Postgres, Redis, API, worker, dashboard, Prometheus, Grafana:

```sh
docker compose up --build        # or: make dev
```

On first boot the api service runs `alembic upgrade head`, seeds the demo
tenant (`python -m relayiq.seed.cli --if-empty`, idempotent), then starts
uvicorn.

| Service | URL |
| --- | --- |
| API | http://localhost:8000 (OpenAPI docs at `/docs`) |
| Dashboard | http://localhost:5173 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (anonymous viewer enabled; admin password from `GRAFANA_ADMIN_PASSWORD`, default `admin` — dev only) |

Demo users (dev-only, documented defaults): `admin@demo.relayiq.test`,
`operator@…`, `reviewer@…`, `analyst@demo.relayiq.test` with password
`relayiq-demo-password`. Override at seed time with
`RELAYIQ_SEED_ADMIN_PASSWORD` etc.

Host-run development (venv API against compose Postgres/Redis):

```sh
make setup      # venv + backend deps + dashboard deps
make dev-deps   # just postgres + redis containers
make migrate    # alembic upgrade head
make seed       # generate synthetic world + seed demo tenant (--reset)
make api        # uvicorn --reload on :8000
make worker     # celery worker (queues: enrichment, sync)
make dashboard  # vite dev server on :5173
```

Quality gates: `make lint`, `make typecheck`, `make test-unit`,
`make test-integration`, `make test-e2e`. Teardown: `make down`
(removes volumes).

Note: compose maps Postgres to host port **5433** (to avoid clashing with a
local Postgres on 5432); inside the compose network it is `postgres:5432`.

## 2. Fly.io deploy

Full detail (including the Terraform-vs-flyctl tradeoff, cost, and managed
DB options) lives in `infrastructure/terraform/README.md`. Those files are
**templates — nothing has been applied to any Fly account**. Short version:

```sh
cd apps/api
fly apps create relayiq-api --org <org>
fly apps create relayiq-worker --org <org>

# Provision state: Fly Postgres or Neon (free tier) for Postgres,
# Upstash (free tier) for Redis. Get both URLs.

# Secrets BEFORE first deploy (repeat for --app relayiq-worker):
fly secrets set --app relayiq-api \
  DATABASE_URL='postgresql+psycopg://USER:PASS@HOST:5432/DB?sslmode=require' \
  REDIS_URL='rediss://.../0' \
  CELERY_BROKER_URL='rediss://.../1' \
  CELERY_RESULT_BACKEND='rediss://.../2' \
  RELAYIQ_JWT_SECRET="$(openssl rand -hex 32)" \
  RELAYIQ_WEBHOOK_SECRETS="$(openssl rand -hex 32)"

fly deploy . --app relayiq-api    --config ../../infrastructure/terraform/fly.api.toml    --primary-region iad
fly deploy . --app relayiq-worker --config ../../infrastructure/terraform/fly.worker.toml --primary-region iad
```

The API config's `release_command` runs `alembic upgrade head` before new
machines take traffic; the worker deploys second (it shares the schema).
Expected cost for a small setup: **$0–25/mo** (see the Terraform README for
the breakdown and caveats).

## 3. Environment variables

Source of truth: `apps/api/relayiq/config.py`. Secrets must only ever arrive
via environment (compose env, `fly secrets set`, CI service-container env) —
never committed.

| Variable | Default (dev) | Required in prod | Purpose |
| --- | --- | --- | --- |
| `RELAYIQ_ENV` | `development` | yes — set `production` | environment gate (`is_production`) |
| `RELAYIQ_LOG_LEVEL` | `INFO` | no | log verbosity |
| `DATABASE_URL` | local compose Postgres | **yes** | SQLAlchemy/psycopg3 URL |
| `REDIS_URL` | `redis://localhost:6379/0` | **yes** | field cache, idempotency, locks |
| `CELERY_BROKER_URL` | `redis://localhost:6379/1` | **yes** | Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/2` | **yes** | Celery results |
| `RELAYIQ_JWT_SECRET` | `dev_only_jwt_secret_do_not_use_in_prod` | **yes — random 32+ bytes** | signs auth tokens |
| `RELAYIQ_JWT_TTL_SECONDS` | `28800` | no | token lifetime |
| `RELAYIQ_WEBHOOK_SECRETS` | `dev_only_webhook_secret` | **yes — random** | HMAC verification; comma-separated, newest first (rotation) |
| `RELAYIQ_WEBHOOK_REPLAY_WINDOW_SECONDS` | `300` | no | webhook replay tolerance |
| `RELAYIQ_SYNTHETIC_WORLD_PATH` | `./data/synthetic_world.json` | no | simulator world file (`/app/data/...` in containers) |
| `RELAYIQ_PROVIDER_SIM_SEED` | `42` | no | deterministic simulator seed |
| `HUBSPOT_ACCESS_TOKEN` | empty | only for live HubSpot | unused while CRM is simulated |
| `HUBSPOT_BASE_URL` | `https://api.hubapi.com` | no | HubSpot endpoint |
| `RELAYIQ_METRICS_ENABLED` | `true` | no | expose `/metrics` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | no | OTLP trace export (off when empty) |
| `OTEL_SERVICE_NAME` | `relayiq-api` | no | trace service name |
| `RELAYIQ_SEED_{ADMIN,OPERATOR,REVIEWER,ANALYST}_PASSWORD` | `relayiq-demo-password` | if seeding prod-like envs | demo-user passwords at seed time |

## 4. Production configuration guidance

- **JWT secret**: generate randomly (`openssl rand -hex 32`). The dev default
  is intentionally named `dev_only_..._do_not_use_in_prod`.
- **Webhook secrets**: random per environment; rotate by prepending the new
  secret to the comma-separated list, then removing the old one after clients
  migrate (verification tries every listed secret).
- **Dev login**: `POST /v1/auth/login` is a documented development substitute
  for real OAuth/SSO (email + password against seeded users). Before exposing
  a production tenant: front it with SSO/OAuth or restrict it, do not seed
  demo users (or set strong `RELAYIQ_SEED_*_PASSWORD` values), and set
  `RELAYIQ_ENV=production`.
- **CORS**: allowed origins are hardcoded to `localhost:5173` for dev; add the
  real dashboard origin before pointing a hosted dashboard at the API.
- **Grafana**: the compose file enables anonymous viewer access — dev only.
  Set a real `GRAFANA_ADMIN_PASSWORD` and disable anonymous access anywhere
  shared.
- **Never** commit `.env` files or tfvars (already gitignored).

## 5. Health & observability endpoints

| Endpoint | Meaning |
| --- | --- |
| `GET /healthz` | liveness — process is up (no dependencies checked) |
| `GET /readyz` | readiness — checks database and Redis; use for load-balancer/deploy health checks (Fly config uses this) |
| `GET /metrics` | Prometheus exposition (`relayiq_*` series; toggle via `RELAYIQ_METRICS_ENABLED`) |
| `GET /docs`, `GET /openapi.json` | interactive API docs |

Local Prometheus scrapes `api:8000/metrics` every 15s
(`infrastructure/prometheus/prometheus.yml`); Grafana auto-provisions the
"RelayIQ Overview" dashboard
(`infrastructure/grafana/dashboards/relayiq-overview.json`).

## 6. Backup & rollback

- **Rollback (app)**: `fly releases --app relayiq-api` then
  `fly releases rollback` (or redeploy a previous image tag). Locally:
  redeploy the previous git tag/image.
- **Rollback (schema)**: `alembic downgrade -1` against the target
  `DATABASE_URL` — only when the downgrade is data-safe; prefer forward-fix
  migrations for anything destructive. App rollback does **not** undo
  migrations by itself.
- **Backups**: Neon has built-in point-in-time restore; Fly Postgres needs a
  scheduled `pg_dump` (cron/scheduled machine) shipped to object storage —
  volume snapshots alone are not a managed guarantee. Redis holds only cache
  and queue state and is treated as ephemeral; Postgres is the source of
  truth (ledger, entities, review, sync attempts).
- **Local**: `docker compose down` keeps volumes; `make down` (`-v`) wipes
  them. `pg_dump` against `localhost:5433` for ad-hoc local backups.
