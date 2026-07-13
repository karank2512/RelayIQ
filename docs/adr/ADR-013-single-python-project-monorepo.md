# ADR-013: Single Python project with subpackages (no packages/* split)

## Status

Accepted

## Date

2026-07-13

## Context

The monorepo contains one Python backend (`apps/api`) and one TypeScript dashboard
(`apps/dashboard`). The backend spans several conceptually separable layers — provider
SDK, decision engines, services, API, workers, benchmark harness, seed tooling. A
common monorepo pattern is to split such layers into independently versioned pip
projects under `packages/*` (e.g. `packages/provider-sdk`, `packages/engines`) with
the app depending on them. We had to decide whether that machinery pays for itself
here.

## Decision

**One installable Python project** — `apps/api/pyproject.toml`, package name
`relayiq`, version 0.1.0 — **with layers as subpackages** of `relayiq`:

```
relayiq/
  api/            # FastAPI routers + deps
  engines/        # decision, routing, reconciliation, confidence, orchestrator
  services/       # cache, budget, ledger, idempotency, crm*, webhook_security, ssrf, ...
  providers/      # base (SDK contract), simulators, registry
  models/         # SQLAlchemy models (32 tables)
  canonical/      # normalization/validation
  workers/        # Celery app + tasks
  benchmark/      # strategy benchmark + calibration eval
  seed/           # world generator + demo seeding
  observability/  # metrics, tracing
  schemas/        # Pydantic API contracts
```

One `pyproject.toml`, one dependency set, one Alembic migration chain, one pytest
tree (`tests/unit|integration|e2e`), one ruff/mypy configuration, one Docker image
(API and worker are the same image with different commands — docs/deployment.md).
Layering is expressed by **import direction convention** (providers/base imports
nothing above it; engines import providers and services; api/workers import engines),
not by packaging boundaries. The dashboard stays a separate npm project because it is
a different toolchain, not because of versioning needs.

## Alternatives considered

- **`packages/*` pip projects with local path/workspace dependencies** — buys enforced
  dependency boundaries and independent versioning. Costs: N pyprojects to keep
  consistent, editable-install graphs in Docker and CI, cross-package version bumps for
  every change that crosses a boundary, and slower "change SDK + engine + test"
  loops. With exactly one consumer (the API/worker image) and one team, independent
  versioning has zero users.
- **Publish the provider SDK separately** (so third parties can write adapters) —
  premature: the adapter contract (`relayiq/providers/base.py`) has two in-tree
  simulator implementations and zero external ones. Extraction is mechanical later
  because `providers/base.py` already only depends on `relayiq/enums.py`.
- **src-layout single project** — cosmetic variant of the decision; plain package
  layout kept for shorter paths in a codebase this size.
- **Import-linter–style contract enforcement** — considered as a lightweight middle
  ground for the layering convention; not adopted yet (revisit condition).

## Consequences

- Refactors that cross layers (routing change + orchestrator + tests) are single
  commits; `pip install -e apps/api` is the entire dev setup for Python.
- CI is one matrix cell for Python (lint, typecheck, 302 tests) instead of a
  package build graph.
- Nothing stops `relayiq.providers` from importing `relayiq.services` except review —
  layering violations are cheap to introduce and only convention catches them.
- The benchmark and seed tooling ship inside the production image (they are
  subpackages of the installed distribution). Acceptable at this scale; they pull in
  no extra runtime dependencies.
- Anyone wanting *just* the provider SDK must take (or extract) the whole package.

## Risks

- Layer erosion over time without mechanical enforcement of import direction.
- A future second consumer (e.g. a standalone CLI distributed to customers) would
  force an extraction under time pressure rather than by design.
- Single dependency set means benchmark-only deps would bloat the runtime image if
  ever added carelessly (today `locust` and friends are correctly in the `dev` extra).

## Revisit conditions

- A second deployable Python artifact with a different dependency footprint.
- External adapter authors → extract `relayiq.providers.base` (+ `enums`) into a
  published `relayiq-provider-sdk` package.
- Recurring layering violations in review → adopt import-linter contracts in CI.
