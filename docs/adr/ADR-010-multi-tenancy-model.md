# ADR-010: Shared-schema multi-tenancy with application-level scoping

## Status

Accepted

## Date

2026-07-13

## Context

RelayIQ serves multiple tenants (RevOps teams / agency clients) from one deployment.
Tenant data — contacts, observations, jobs, ledgers — must never leak across tenants,
but the target scale for this build (a pilot-sized control plane, one Postgres, one
Redis) does not justify the operational cost of schema-per-tenant or
database-per-tenant. Some data is deliberately *not* tenant-scoped: the provider
catalog and default staleness policies are platform-level configuration.

## Decision

**One shared PostgreSQL schema; every tenant-owned row carries a `tenant_id`.**

- `relayiq/models/base.py::TenantMixin` adds
  `tenant_id: FK tenants.id ON DELETE CASCADE, NOT NULL, indexed` to every
  tenant-owned model. Composite indexes lead with `tenant_id`
  (e.g. `ix_jobs_tenant_status`, `ix_obs_entity_field`), so tenant filtering is also
  the access path.
- `relayiq/models/tenancy.py` defines `Tenant` (unique `slug`), `User`
  (unique `(tenant_id, email)`), plus tenant-scoped `AuditEvent`, `PolicyDecision`,
  and `Suppression`.

**JWT claims are re-verified against the database on every request.**
`relayiq/api/deps.py::current_principal` decodes the bearer token
(`relayiq/security.py`, HS256), then loads the `users` row and rejects the request
unless the user exists, `is_active`, and `user.tenant_id == claims.tenant_id`. The
`Principal`'s role and tenant come **from the DB row, not the token payload** —
revoking a user or changing a role takes effect immediately, within token lifetime.
The resolved tenant is stamped into the logging context (`tenant_id_var`).

**Redis isolation is by key prefix, not ACL.** `relayiq/services/cache.py::FieldCache`
builds every key as `riq:{schema_version}:{tenant_id}:...`
(positive `:f:`, negative `:neg:`, locks `riq:{ver}:lock:{tenant}:...` — ADR-003).
A tenant's cache entries are unreachable from another tenant's code path because the
tenant id in the key comes from the verified principal, never from request input.

**Deliberately global (not tenant-scoped):**

- `provider_configs` / `provider_capabilities` (`relayiq/models/providers.py`):
  the provider registry is a **global catalog** shared across tenants
  (`ProviderConfig.tenant_id` is nullable — `NULL` = available to all tenants — and
  `key` is globally unique). Provider health windows, circuit breakers, and rate
  limiters are likewise per-provider, not per-tenant: one tenant's traffic
  degrades/opens the breaker for everyone, which is correct for a shared upstream.
- `staleness_policies` support `tenant_id NULL` global defaults with per-tenant
  override rows (`relayiq/services/staleness.py` precedence: tenant > global >
  builtin).
- Prometheus metric labels contain **no tenant ids** by design
  (`relayiq/observability/metrics.py`) — bounded cardinality beats per-tenant
  dashboards at this scale. Per-tenant analytics come from Postgres
  (`/v1/metrics/*`), which is tenant-scoped through the principal.

## Alternatives considered

- **Schema-per-tenant** — strong isolation, but N× migrations, N× Alembic state, and
  painful cross-tenant platform queries (provider health, global benchmarks) for a
  pilot-scale product.
- **Database-per-tenant** — maximal isolation, maximal operational cost; rules out
  the single docker-compose / single Fly.io deployment target (docs/deployment.md).
- **Postgres Row-Level Security** — attractive defense-in-depth on top of the shared
  schema; not adopted yet because the app uses one DB role and RLS would require
  per-request `SET`s through the pooler. Listed as a revisit condition, not an
  alternative to application scoping.
- **Tenant id inside the JWT only (no DB check)** — cheaper per request, but tokens
  would stay valid after deactivation/role change and a signing-key leak would mint
  tenants. Rejected; the DB re-verification is the mitigation for both.

## Consequences

- One migration chain, one connection pool, trivial cross-tenant platform analytics.
- Tenant isolation is **code discipline**: every query must filter on
  `principal.tenant_id`. Router handlers do this explicitly (e.g.
  `relayiq/api/routers/enrichment.py::get_job` returns 404 when
  `job.tenant_id != principal.tenant_id`); `tests/integration/test_auth_and_tenancy.py`
  covers the cross-tenant cases.
- `ON DELETE CASCADE` from `tenants` means tenant offboarding is a single row delete
  (plus a Redis `SCAN` prefix sweep via `FieldCache.invalidate_entity`-style cleanup).
- The webhook endpoint resolves the tenant from the **payload's `tenant_slug`**
  under a globally shared HMAC secret set (`relayiq/api/routers/webhooks.py`), so any
  holder of a valid webhook secret can enqueue enrichment for any tenant — accepted
  and documented for this build (SECURITY.md, threat model §1).

## Risks

- A missing `tenant_id` filter in one query is a cross-tenant read. No RLS backstop
  exists today.
- Shared provider rate limits / breakers let one noisy tenant degrade others
  (no per-tenant fairness).
- Redis prefix isolation assumes nobody else talks to that Redis; there is no
  per-tenant Redis ACL.

## Revisit conditions

- Enabling Postgres RLS (`tenant_id = current_setting(...)`) as defense-in-depth once
  connection handling supports it.
- Per-tenant webhook secrets (removes the shared-secret/slug limitation).
- Any real-customer deployment with contractual isolation requirements
  (→ schema-per-tenant at minimum).
- Per-tenant provider quotas if noisy-neighbor contention shows up in the ledger.
