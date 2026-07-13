# Contributing to RelayIQ

Thanks for looking at the code. This document covers local setup, how to run the test
suites, style rules, the ADR process, and PR conventions. The layout: backend lives in
`apps/api` (Python package `relayiq`), the React dashboard in `apps/dashboard`, shared
infra in `docker-compose.yml` and `infrastructure/`.

## Setup

```bash
git clone <repo> && cd RelayIQ
cp .env.example .env      # dev defaults work out of the box
make setup                # venv at apps/api/.venv, installs relayiq[dev] + dashboard npm deps
make dev-deps             # docker compose up -d postgres redis
make migrate              # alembic upgrade head
make seed                 # synthetic world + demo tenant (idempotent, --reset)
```

Postgres is published on host port **5433** (deliberately, to avoid clashing with a local
Postgres on 5432) and Redis on **6379** — the defaults in `relayiq/config.py` and the test
suite both assume this. Then run the pieces you need:

```bash
make api          # uvicorn on :8000
make worker       # celery worker (queues: enrichment, sync)
make dashboard    # vite dev server on :5173
```

Or run everything containerized with `make dev` (adds Prometheus :9090, Grafana :3000).
Demo logins are seeded one per role (`*@demo.relayiq.test` / `relayiq-demo-password`,
dev-only). All seeded data is synthetic — `.test` domains, invented names. Keep it that
way: never commit real personal data, real CRM records, or real provider responses.

## Tests

```bash
make test              # unit tests (no services required)
make test-integration  # REQUIRES docker compose postgres (:5433) + redis (:6379)
make test-e2e          # the 12 end-to-end scenarios — same service requirements
make test-all          # everything
```

Integration and e2e tests run against the real Postgres and Redis from `make dev-deps` —
they will fail fast if the containers aren't up. The e2e file
(`apps/api/tests/e2e/test_scenarios.py`) is the behavioral contract: duplicate webhooks,
budget blocks, provider outage fallback, review reversal, CRM overwrite protection.
If you change pipeline behavior, update or extend a scenario rather than deleting one.

Benchmarks and load tests are reproducible artifacts, not CI gates:

```bash
make benchmark    # regenerates docs/benchmarks/results.{json,md}
make calibration  # regenerates docs/benchmarks/calibration.{json,md}
make load-test    # locust, 25 users / 60s, local numbers only
```

Dashboard Playwright specs live in `apps/dashboard/e2e/` (`npx playwright test` from
`apps/dashboard`, with the seeded stack running).

## Style

- **Python**: `ruff` with **line length 110** (config in `apps/api/pyproject.toml`) and
  `mypy` for types. Run `make fmt` before committing; `make lint` and `make typecheck`
  must pass clean — the repo is currently lint-clean and should stay that way.
- **TypeScript**: `npm run lint` (eslint) and `npm run typecheck` in `apps/dashboard`.
- Docstrings on modules that encode decisions (see `relayiq/engines/*.py` for the house
  style: the docstring states the mechanism and points at its ADR).
- Honesty rules apply to docs and code comments alike: measured numbers must come from a
  regenerable artifact in `docs/benchmarks/`, simulated things are labeled simulated, and
  integration claims match the README's integration-status table.

## ADR process

Architecture decisions live in `docs/adr/` as `ADR-NNN-short-slug.md`, numbered
sequentially (currently ADR-001 through ADR-013). Write one when a change: picks between infrastructure
alternatives, changes a durability/consistency guarantee, alters the schema's role, or
reverses a previous ADR. Use the existing structure — Status, Date, Context, Decision,
Alternatives considered, Consequences, Risks, and (encouraged) explicit **Revisit
conditions**, which downstream docs treat as triggers (see `docs/roadmap.md` item 7).
Supersede rather than edit: a reversed decision gets a new ADR that names the old one,
and the old ADR's status becomes `Superseded by ADR-NNN`.

## Pull requests

- Branch from `main`; name branches `feature/…`, `fix/…`, or `docs/…`.
- Keep PRs single-purpose. Schema changes ship with an Alembic migration (and downgrade),
  behavior changes ship with tests, decision-level changes ship with an ADR.
- PR description: what changed, why, how it was verified (paste the test commands you
  ran). If a measured number in README/docs is affected, regenerate it (`make benchmark`
  / `make calibration`) in the same PR — never hand-edit a measured number.
- Pre-push checklist: `make fmt lint typecheck test`, plus `test-integration`/`test-e2e`
  when touching the pipeline, and the Playwright suite when touching the dashboard.
- No secrets in code, fixtures, or docs — the only permitted literal secrets are the
  documented dev-only defaults (`dev_only_webhook_secret`, `relayiq-demo-password`).
- Commit messages: imperative mood, ≤72-char subject; reference the ADR when applicable.
