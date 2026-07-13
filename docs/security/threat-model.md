# RelayIQ threat model

Scope: the backend control plane (`apps/api`) as deployed per `docs/deployment.md` —
FastAPI API + Celery worker + PostgreSQL + Redis, providers **simulated** (ADR-009).
Status: internally reviewed only; this build has **not** had an external security
review (see `SECURITY.md`). Each threat below maps to the implemented mitigation
(with the module that implements it) and the residual risk we knowingly carry.

## Summary table

| # | Threat | Implemented mitigation (module) | Residual risk |
|---|--------|--------------------------------|---------------|
| 1 | Forged webhooks | HMAC-SHA256 over raw body, `t=,v1=` header, constant-time compare (`relayiq/services/webhook_security.py`; enforced first in `relayiq/api/routers/webhooks.py`) | One global secret set; tenant chosen by payload slug → a valid signer can enqueue for any tenant |
| 2 | Webhook replay | Signed timestamp; ±window rejection (300 s past / 60 s future) (`webhook_security.py`) | Replays inside the window need dedup (§3) to be harmless |
| 3 | Duplicate deliveries | DB unique `(tenant_id, source, delivery_id)`; duplicate returns original job, zero spend (`relayiq/models/webhooks.py`, `api/routers/webhooks.py`) | Dedup table grows unboundedly; a secret holder can mint fresh delivery ids |
| 4 | Credential exposure | Secrets env-only (`relayiq/config.py`); log redaction (`relayiq/logging_setup.py`); gitleaks in CI (`.github/workflows/security.yml`); CRM creds excluded from DB config (`relayiq/models/crm.py`) | Dev-default secrets exist; no secret manager integration; bcrypt+HS256 dev auth |
| 5 | Cross-tenant access | JWT re-verified against `users` row; tenant/role from DB (`relayiq/api/deps.py`); `tenant_id` on every owned row (`relayiq/models/base.py::TenantMixin`); per-handler tenant filters | App-level discipline only — no Postgres RLS backstop; provider configs/metrics intentionally global |
| 6 | Injection (SQL/payload) | SQLAlchemy bound parameters throughout; strict Pydantic schemas with field allowlists and size caps (`relayiq/schemas/enrichment.py`) | JSONB passthrough columns (`metadata_passthrough`) store attacker-chosen content (size-capped) |
| 7 | Broken authorization | Role hierarchy + `require_role` dependencies; roles never read from headers (`relayiq/api/deps.py`, `relayiq/security.py`) | Coarse roles; no per-object ACLs beyond tenant |
| 8 | SSRF via callback URLs | Validation at intake (`api/routers/enrichment.py::_check_callback`) **and re-validation at send time** (`relayiq/workers/tasks.py::_maybe_callback`) using `relayiq/services/ssrf.py` | validate-then-fetch TOCTOU: `httpx.post` does not pin the resolved IP |
| 9 | Malicious provider payloads | Normalization + format validation before storage (`relayiq/canonical/normalize.py` via `engines/orchestrator.py::_persist_observation`); error-normalized adapter contract (`providers/base.py`) | Real-vendor payload handling untested (simulators only); raw payload retention rules are policy (ADR-012) |
| 10 | Sensitive data in logs | structlog redaction processor: secret-key redaction + email masking (`relayiq/logging_setup.py`); webhook failures log reason class only | Regex/key-based redaction misses nested or renamed fields; Redis cache keys embed emails (ADR-003) |
| 11 | Retry storms | Bounded adapter retries (`providers/base.py::RetryPolicy`, `services/provider_exec.py`); circuit breaker (5 failures → open, 30 s cooldown, `providers/registry.py`); ≤4 fallback rounds (`engines/orchestrator.py`); Celery `max_retries=3` w/ backoff (`workers/tasks.py`) | Breaker + limiter are per-process, not shared across workers |
| 12 | Budget exhaustion | Atomic guarded-UPDATE reservation: `spent+reserved+X <= limit` evaluated in the DB (`relayiq/services/budget.py::reserve`); per-record caps; degradation modes | Soft budgets never block (by design); no budget = unlimited spend for that scope |
| 13 | Cache poisoning | Keys scoped `riq:{schema_version}:{tenant_id}:...`; Postgres remains source of truth (ADR-002); corrupt JSON → miss; version bump orphans all entries (`relayiq/services/cache.py`) | Anyone with Redis network access can write keys — isolation is prefix convention, not ACL |
| 14 | Race conditions / double-spend | DB unique constraints: idempotency `(tenant,scope,key)` (`services/idempotency.py`), webhook delivery (§3), CRM sync `(tenant, idempotency_key)` (`models/crm.py`); one-open-review-task upsert (`services/review.py::create_task`); lock token compare-and-delete Lua (`services/cache.py`) | Idempotency expiry re-claims are "race-safe enough" per code comment, not strictly serialized |
| 15 | Accidental CRM overwrites | Per-field sync gate: write / no_write / secondary_property / require_approval / preserve_crm / mark_refresh with stored reasons (`relayiq/services/crm_gate.py`, ADR-008); manual field locks | Gate correctness proven against the CRM simulator + HubSpot fixtures only — no live CRM verification |
| 16 | Supply chain | CI: `pip-audit` on resolved env, gitleaks full-history secret scan, Trivy image scan CRITICAL/HIGH (`.github/workflows/security.yml`) | pip-audit and Trivy are advisory (`continue-on-error`); no dependency pinning/lockfile; no SBOM |

## Details and reasoning

### 1–3. Webhooks: forgery, replay, duplication

The webhook endpoint is the cheapest path to making RelayIQ spend money, so it gets
the strictest pipeline (ADR-011): verify HMAC on the **raw** body → check the signed
timestamp against the replay window → require `X-Delivery-Id` → insert the delivery
row and let the unique constraint arbitrate duplicates → only then parse. All digest
comparisons use `hmac.compare_digest` across every (secret, candidate) pair without
short-circuiting, so timing reveals nothing about rotation state. A duplicate
delivery returns `duplicate: true` with the original `job_id` and spends nothing —
this exact behavior is covered by the e2e duplicate-webhook scenario.

**Accepted weakness (documented, not hidden):** secrets are deployment-global and the
tenant is resolved from `payload.tenant_slug`. Isolation between *senders* therefore
does not exist at the webhook layer; budgets (§12) bound the damage. Per-tenant
secrets are the planned fix (ADR-011 revisit conditions).

### 4. Credential exposure

`relayiq/config.py` sources every secret from the environment; there are no secrets
in the repo (gitleaks scans full history in CI to keep it that way). The structlog
pipeline redacts values under keys like `password`, `token`, `secret`, `api_key`,
`signature`, `hubspot_access_token` and masks email local-parts. `CrmConnection.config`
is documented as non-secret configuration only — credentials live in env. Dev
defaults (`dev_only_jwt_secret_do_not_use_in_prod`, `dev_only_webhook_secret`) are
deliberately obvious strings; production deployment docs require overriding them.

### 5, 7. Tenancy and authorization

Every request principal is rebuilt from the database
(`relayiq/api/deps.py::current_principal`): token decoded, then the `users` row must
exist, be active, and match the token's tenant — role comes from the row, never the
token or a header. Handlers filter by `principal.tenant_id` and return 404 (not 403)
for other tenants' objects, avoiding existence oracles. `ROLE_ORDER`
(analyst < reviewer < operator < admin) backs `require_role` dependencies per router.
The absence of RLS means one forgotten filter is a leak; the integration suite
(`tests/integration/test_auth_and_tenancy.py`) exercises the cross-tenant paths.

### 8. SSRF via callback URLs

`relayiq/services/ssrf.py` rejects: non-http(s) schemes, userinfo in the netloc,
ports outside {80, 443, 8000–8999}, IP literals (including bare-integer and
IPv4-mapped-IPv6 encodings) in private/loopback/link-local/reserved/multicast
ranges, the cloud metadata address 169.254.169.254, blocked names
(`localhost`, `*.internal`, `*.local`), and hostnames where **any** resolved
A/AAAA record is forbidden (conservative DNS-rebinding stance). Validation runs at
intake (`_check_callback` → 422) and **again at send time** in
`workers/tasks.py::_maybe_callback` — DNS may legitimately change between job
submission and completion, so the worker re-runs the full check before dialing and
suppresses the callback on failure (TOCTOU defense). Residual: the actual `httpx.post`
resolves DNS itself rather than pinning the vetted address; the ssrf module's
docstring flags this explicitly and `tests/unit/test_ssrf.py` covers the validator.

### 9. Malicious provider payloads

The adapter contract (`providers/base.py`) requires providers to normalize all errors
into `EnrichmentCallResult` — adapter exceptions never propagate into the pipeline.
Every returned field value passes through `normalize_value` and `validate_field`
before persistence (`orchestrator.py::_persist_observation`); validation results ride
on the observation and feed the confidence `format` component, and reconciliation can
`reject_all` observations that fail validation. Values land in typed/bounded columns
(`raw_value` Text, `normalized_value` String(1000)) — nothing is executed or
interpolated. Since all current providers are simulators, hostile-vendor behavior is
untested against real network responses; that limitation is stated wherever the
benchmark is cited.

### 10. Sensitive data in logs

Beyond the redaction processor, sensitive call sites are individually careful: the
webhook router logs only the rejection reason class; worker callback failures log the
exception type, not the URL or body. Known residuals: redaction is key-pattern based
(a nested dict under an innocuous key escapes it), and Redis key names contain
plaintext emails/domains (accepted in ADR-003, revisit for compliance).

### 11–12. Retry storms and budget exhaustion

Defense in depth on the spend path: per-call retries are bounded (default
`max_retries=2`, exponential backoff) and only for retryable outcomes; consecutive
retryable failures open a per-provider circuit breaker (threshold 5, cooldown 30 s)
which the router also consults when scoring candidates; orchestrator fallback
re-routing is capped at 4 rounds; the Celery task retries at most 3 times then parks
the job as `failed`. Before any provider call, the expected cost is **reserved** via a
single guarded UPDATE whose predicate (`spent + reserved + amt <= limit`) executes in
the database — concurrent jobs cannot jointly overshoot a hard budget
(`tests/integration/test_concurrency.py`). Warning thresholds flip campaigns into
degradation modes (`cheapest` / `cache_only` / `required_fields_only`) before hard
blocks hit.

### 13–14. Cache poisoning and races

Redis is a disposable acceleration layer: canonical values live in Postgres
(ADR-002), cache keys are tenant- and schema-version-scoped, corrupt entries count as
misses, and review reversals explicitly invalidate affected keys
(`services/review.py`). Correctness under concurrency is anchored in the database,
not application locks: unique constraints arbitrate idempotency claims, webhook
dedup, and CRM sync attempts; the only distributed lock (cache refresh) uses a random
token with Lua compare-and-delete release so a worker can never free another's lock.

### 15. Accidental CRM overwrites

The sync gate (ADR-008) makes "write to CRM" the *hardest* outcome to reach: a field
must have a canonical value, no unresolved conflict, confidence ≥ threshold (or an
explicit reviewer approval), acceptable staleness, and must beat the existing CRM
value's freshness — otherwise it is preserved, routed to a secondary property for
human comparison, queued for approval, or marked for refresh. Every decision stores
its reasons on the `crm_sync_attempts` row.

### 16. Supply chain

`.github/workflows/security.yml` runs three scanners: `pip-audit` against the
resolved production environment (transitive deps included), gitleaks across full git
history, and Trivy against the built API image (CRITICAL/HIGH, ignore-unfixed).
pip-audit and Trivy are advisory (`continue-on-error: true`) so unfixable upstream
CVEs don't permanently red the pipeline — meaning a red scan requires a human to
look, which is a staffing assumption, not a technical control.

## Out of scope for this build

- DoS beyond retry/budget bounding (no API rate limiting per client).
- Compromise of the host, Postgres, or Redis themselves (single-box dev posture).
- Real provider / live CRM traffic (simulated; HubSpot adapter fixture-tested only).
- Formal verification of the dashboard (`apps/dashboard`) beyond standard React/JSX
  escaping — it renders API data only.
