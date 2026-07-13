# RelayIQ — Fly.io deployment templates

> **Status: TEMPLATES ONLY — NOTHING HAS BEEN APPLIED.**
> No `terraform apply` was run, no Fly.io app, database, or Redis instance was
> created, and no secrets were set anywhere. Every command below is a
> documented procedure, not a record of something that happened.

## Why `null_resource` + flyctl instead of a Fly Terraform provider

Fly.io does not ship an official, maintained Terraform provider. The community
provider (`fly-apps/fly`) is **archived and unmaintained**: it predates much of
the current Machines API, and Fly's own documentation points users at `flyctl`
or the Machines API directly. Pinning production infrastructure to an abandoned
provider is worse than being honest about the gap, so this config uses
`null_resource` provisioners that shell out to `flyctl` — Terraform keeps
variable wiring, ordering (worker deploys after the API so the release-command
migration has run), and create/destroy lifecycle; `flyctl` does the real work.
If Fly ships a supported provider later, replace the two `null_resource`s with
real resources; `variables.tf` is already shaped for that swap.

**Simpler alternative (recommended for a first deploy):** skip Terraform
entirely and use `flyctl` directly — see "Deploying with flyctl alone" below.
Terraform adds value here mainly when RelayIQ is one piece of a larger,
already-Terraformed estate.

## Files

| File | Purpose |
| --- | --- |
| `main.tf` | Two `null_resource`s wrapping `fly apps create` + `fly deploy` for api and worker |
| `variables.tf` | `app_name`, `fly_org`, `region`, `api_image`, `worker_image` — no env-specific values hardcoded |
| `fly.api.toml` | Example Fly config for the API (release command runs migrations, `/readyz` health check) |
| `fly.worker.toml` | Example Fly config for the Celery worker (no public service, no scale-to-zero) |

## Prerequisites

- `flyctl` installed and authenticated (`fly auth login`)
- Terraform >= 1.6 (only for the Terraform path)
- A built/pushed image, or let `fly deploy` build from `apps/api/Dockerfile`

## Deploying with flyctl alone (no Terraform)

```sh
cd apps/api

# One-time: create apps (or `fly launch --no-deploy` and adapt its generated toml)
fly apps create relayiq-api    --org personal
fly apps create relayiq-worker --org personal

# Set secrets FIRST (see the table below), then:
fly deploy . --app relayiq-api    --config ../../infrastructure/terraform/fly.api.toml    --primary-region iad
fly deploy . --app relayiq-worker --config ../../infrastructure/terraform/fly.worker.toml --primary-region iad
```

## Deploying with Terraform

```sh
cd infrastructure/terraform
terraform init
terraform plan  -var app_name=relayiq -var region=iad -var fly_org=personal
terraform apply -var app_name=relayiq -var region=iad -var fly_org=personal
```

Never commit `*.tfvars` with real values (the repo `.gitignore` already
excludes them).

## Managed Postgres and Redis

Postgres is deliberately **not** modeled in `main.tf` — database creation is a
one-time stateful operation that a re-runnable provisioner handles badly. Pick
one:

- **Fly Postgres** (unmanaged, cheapest): `fly postgres create --name relayiq-db --region iad`
  then `fly postgres attach relayiq-db --app relayiq-api` (attach prints a
  connection string; convert it to SQLAlchemy form, below). Single-node dev
  config is ~$2–5/mo on a shared-cpu-1x + small volume.
- **Neon free tier** (managed, $0): create a project at neon.tech, copy the
  connection string. Free tier suits demo/light workloads; note cold starts on
  the free compute.

Redis:

- **Upstash Redis via Fly** (`fly redis create`) or directly at upstash.com —
  the free tier is fine for the demo cache + Celery broker volume.

The app expects **SQLAlchemy/psycopg3 URL form**:
`postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME` (append
`?sslmode=require` for Neon/external hosts).

## Secrets — `fly secrets set` (required env vars)

All values come from `apps/api/relayiq/config.py`. Set on **both** apps
(`--app relayiq-api` and `--app relayiq-worker`) since worker and API share
config. Do not put any of these in the toml `[env]` blocks or in any file.

```sh
fly secrets set --app relayiq-api \
  DATABASE_URL='postgresql+psycopg://...' \
  REDIS_URL='rediss://...' \
  CELERY_BROKER_URL='rediss://.../1' \
  CELERY_RESULT_BACKEND='rediss://.../2' \
  RELAYIQ_JWT_SECRET="$(openssl rand -hex 32)" \
  RELAYIQ_WEBHOOK_SECRETS="$(openssl rand -hex 32)"
# repeat with --app relayiq-worker (same values)
```

| Env var | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL` | yes | SQLAlchemy psycopg3 URL |
| `REDIS_URL` | yes | cache; db 0 |
| `CELERY_BROKER_URL` | yes | broker; db 1 |
| `CELERY_RESULT_BACKEND` | yes | results; db 2 |
| `RELAYIQ_JWT_SECRET` | yes | generate randomly; never the dev default |
| `RELAYIQ_WEBHOOK_SECRETS` | yes | comma-separated, newest first (rotation-friendly) |
| `RELAYIQ_ENV` | no (toml `[env]`) | `production` disables dev affordances |
| `RELAYIQ_SYNTHETIC_WORLD_PATH` | no (toml `[env]`) | simulator world file path |
| `HUBSPOT_ACCESS_TOKEN` | only for live HubSpot | providers are simulated in this build |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | no | leave empty to disable trace export |

See `docs/deployment.md` for the full variable table including tuning knobs.

## Expected cost

| Setup | Est. monthly |
| --- | --- |
| Scale-to-zero API + 1 worker (shared-cpu-1x/512MB) + Neon free + Upstash free | **$0–10** |
| Always-on API + worker + single-node Fly Postgres | **~$10–25** |

Estimates from Fly's published shared-cpu pricing (~$2–6/machine-month
region-dependent) as of mid-2026 — verify against fly.io/docs/about/pricing
before relying on them. Not measured from a real deployment (nothing was
deployed).

## Rollback

1. `fly releases --app relayiq-api` — list releases.
2. `fly releases rollback --app relayiq-api` (or redeploy the previous image:
   `fly deploy --app relayiq-api --image registry.fly.io/relayiq-api:<prev-tag>`).
3. **Migrations are not rolled back automatically.** If the bad release
   included a schema migration, run
   `alembic downgrade -1` (from `apps/api`, `DATABASE_URL` pointed at prod,
   e.g. via `fly proxy` / `fly ssh console`) **before** rolling the app back —
   and only if the downgrade is data-safe. Prefer forward-fixes for
   destructive migrations.

## Backups

- **Fly Postgres (unmanaged):** volume snapshots exist (`fly volumes snapshots list`)
  but are not a managed-backup guarantee — add a scheduled `pg_dump`, e.g. a
  cron (scheduled Fly machine or external runner):
  `pg_dump "$DATABASE_URL" | gzip > relayiq-$(date +%F).sql.gz` shipped to
  object storage (Tigris/S3). Test restores periodically.
- **Neon:** point-in-time restore is built in (retention depends on plan).
- **Redis/Upstash:** treat as ephemeral (cache + queues); no backup needed —
  the ledger/source of truth is Postgres.
