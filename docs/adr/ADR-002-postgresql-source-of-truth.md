# ADR-002: PostgreSQL as the single source of truth

## Status

Accepted

## Date

2026-07-11

## Context

RelayIQ's product claims rest on auditability: every decision (route, reconcile,
gate, spend) must be reconstructable after the fact, and money-adjacent state
(budgets, ledgers, idempotency) must be correct under concurrency. The system also
uses Redis heavily. The question is which store owns the truth.

## Decision

**PostgreSQL owns all durable state.** Redis is strictly a cache and message broker;
losing it must never lose facts.

- All 32 tables are SQLAlchemy 2 declarative models under `relayiq/models/`
  (`base.py` provides `PKMixin` UUID strings, `TimestampMixin`, `TenantMixin`), with
  schema managed by Alembic (`apps/api/alembic/versions/` — initial schema plus a
  provider-health/reservoir revision).
- `relayiq/services/cache.py` states the contract in its docstring: "PostgreSQL stays
  the source of truth (ADR-002, ADR-003)". Cache entries are derived from
  `canonical_field_values` / `field_observations` and can always be rebuilt.
- Correctness-critical invariants are enforced *in the database*:
  - Budget reservation is a single guarded `UPDATE ... WHERE spent + reserved + X <= limit`
    (`relayiq/services/budget.py::reserve`) so concurrent reservations cannot jointly
    exceed a hard limit.
  - Idempotency claims ride a unique constraint on `(tenant_id, scope, key)`
    (`ix_idem_unique` in `relayiq/models/enrichment.py`; claim logic in
    `relayiq/services/idempotency.py`).
  - Webhook dedup rides `unique(tenant_id, source, delivery_id)`
    (`ix_webhook_delivery_unique` in `relayiq/models/webhooks.py`).
  - One canonical value per field: `unique(tenant_id, entity_type, entity_id, field_name)`
    (`ix_cfv_unique` in `relayiq/models/entities.py`).
- JSON payloads use `JSONVariant` (`relayiq/models/base.py`): JSONB on Postgres,
  plain JSON on SQLite so unit tests run without Postgres.
- Money columns are `Numeric(…, 4)` (e.g. `budgets.limit_credits`,
  `cost_ledger_entries.actual_cost_credits`), and `relayiq/services/budget.py` does
  arithmetic in `Decimal`.

## Alternatives considered

- **Redis as primary store** — fast but wrong for this domain: no cross-key
  transactions on the shapes needed, and persistence tuning trades away exactly the
  durability the ledger requires.
- **Event sourcing** — the decision tables (`routing_decisions`,
  `reconciliation_decisions`, `workflow_steps`, `audit_events`) already give an
  append-only decision trail without rebuilding all reads through projections.
- **Document store (Mongo/Dynamo)** — the schema is relational (jobs → steps →
  observations → decisions) and the concurrency controls used here (guarded UPDATE,
  unique-constraint claims) are native SQL strengths.

## Consequences

- Redis can be flushed at any time with zero data loss (cache entries re-derive;
  queued Celery tasks are the one exception — see ADR-001 risks).
- Single write node: Postgres is the throughput ceiling; the orchestrator commits at
  each step boundary, which is chatty but keeps recovery simple.
- Unit tests run against SQLite thanks to `JSONVariant` and string enums
  (`relayiq/enums.py` stores values as VARCHAR "for portability").

## Risks

- Step-boundary commits mean more transactions per job than a single-commit design;
  under load this is the first tuning target.
- `latency_reservoir` (JSON list on `provider_health_windows`) is written on every
  provider call — a hot row per provider per 5-minute window.

## Revisit conditions

- Sustained write volume where per-step commits become the bottleneck.
- A second service needs the same data (would motivate CDC/outbox patterns).
- Regulatory retention rules requiring partitioning or archival of ledger/audit tables.
