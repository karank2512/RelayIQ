# Tenancy model and its limits

Full decision record: ADR-010. This page is the operational summary — what is
tenant-scoped, what is deliberately global, and where the boundaries are thinner than
they look.

## The model

**Shared PostgreSQL schema, `tenant_id` on every tenant-owned row.**
`relayiq/models/base.py::TenantMixin` adds an indexed, cascading `tenant_id` FK;
composite indexes lead with `tenant_id`. There is no schema-per-tenant and no
Postgres row-level security — isolation is application-level query discipline,
exercised by `tests/integration/test_auth_and_tenancy.py`.

**Request identity is DB-verified, per request.**
`relayiq/api/deps.py::current_principal` decodes the JWT, then requires a live
`users` row whose `tenant_id` matches the token's claim; the principal's role and
tenant come **from the row, not the token**. Deactivating a user or changing a role
takes effect immediately. Role checks are hierarchy-based
(`analyst < reviewer < operator < admin`) via `require_role` dependencies.

**Redis is prefix-partitioned.** All field-cache keys are
`riq:{schema_version}:{tenant_id}:...` (`relayiq/services/cache.py`, ADR-003). The
tenant id in the key always comes from the verified principal or the job row — never
from request input.

**Budgets, policies, suppressions, review queues, ledgers** are all per tenant (and
usually per campaign) — one tenant's spend, review load, or suppression list is
invisible to another.

## Deliberately global (by design, not omission)

| Global thing | Where | Rationale |
|---|---|---|
| **Provider catalog** (`provider_configs`, `provider_capabilities`) | `relayiq/models/providers.py` | Providers are platform infrastructure. `tenant_id NULL` = available to all tenants; `key` is globally unique. |
| **Provider health, circuit breakers, rate limiters** | `provider_health_windows`, `providers/registry.py`, `providers/simulators.py` | Upstream health is a shared fact: if a provider is down, it is down for everyone. Breakers/limiters are additionally **per-process** (see limits). |
| **Prometheus metric labels are NOT tenant-scoped** | `relayiq/observability/metrics.py` | Deliberate cardinality control — the module's first line forbids tenant IDs as label values. Per-tenant analytics come from Postgres via the tenant-scoped `/v1/metrics/*` API instead. |
| **Staleness policy defaults** | `staleness_policies` with `tenant_id NULL` | Global defaults; tenants override per field (`services/staleness.py` precedence: tenant > global > builtin). |
| **Webhook HMAC secrets** | `RELAYIQ_WEBHOOK_SECRETS` (`relayiq/config.py`) | Single secret set per deployment; see limits below. |

## Known limits (documented, accepted for this build)

1. **A missed `tenant_id` filter is a leak.** No RLS backstop exists. Handlers
   return 404 for other tenants' objects (no existence oracle), but the guarantee is
   convention + tests, not the database.
2. **Webhook tenant resolution by slug under shared secrets.** The webhook payload
   names its tenant (`tenant_slug`) and any holder of a valid deployment-wide secret
   can enqueue enrichment for any tenant (budget-bounded). Documented in ADR-011 and
   SECURITY.md; per-tenant secrets are the planned fix.
3. **No per-tenant fairness on providers.** Shared rate limits and breakers mean a
   noisy tenant can open a breaker or exhaust a rate window for everyone. Per-tenant
   provider quotas are a revisit condition in ADR-010.
4. **Metrics cannot answer per-tenant questions.** By design — use the ledger and
   quality endpoints (`services/ledger.py`, `services/quality.py`), which are
   tenant-scoped and richer anyway.
5. **Redis isolation is convention.** Prefixes, not ACLs; anyone with Redis network
   access sees all tenants' cache keys (which embed emails/domains — ADR-003 risk).
6. **Per-process limiter/breaker state.** Multiple API/worker processes multiply
   effective rate limits and don't share breaker trips (SECURITY.md §3).
7. **Tenant deletion** cascades in Postgres (`ON DELETE CASCADE` from `tenants`) but
   Redis cleanup is a separate prefix sweep — cache entries for a deleted tenant
   otherwise age out via TTL (hard TTL default 6 h).

## Adding a tenant

Seeding (`relayiq/seed/cli.py`) or admin API: create the `tenants` row (unique slug),
users with roles, optionally campaign/budget/policy rows. Nothing provider-side needs
tenant setup — the global catalog applies immediately; per-tenant staleness or
routing policies are optional overrides.
