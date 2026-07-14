# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities **privately** — do not open a public GitHub
issue. Use GitHub's private vulnerability reporting ("Report a vulnerability" under
the repository's Security tab). Include reproduction steps, the affected module path,
and impact as you understand it.

You can expect an acknowledgment within 7 days. Good-faith research against your own
deployment of RelayIQ is welcome; do not test against instances or data you don't own.

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes (current development line) |
| < 0.1   | No |

RelayIQ is pre-1.0. There are no LTS branches; fixes land on `main` and ship in the
next 0.1.x tag.

## Security posture (what is actually implemented)

- Stripe-style HMAC-SHA256 webhook verification over the raw body, constant-time
  comparison, replay window, secret rotation, delivery-ID dedup
  (`apps/api/relayiq/services/webhook_security.py`, ADR-011).
- JWT auth whose claims are re-verified against the `users` table on every request;
  roles come from the DB, never from headers (`apps/api/relayiq/api/deps.py`).
- Tenant scoping (`tenant_id`) on every tenant-owned table and Redis key
  (ADR-010, ADR-003).
- SSRF validation of callback URLs at intake **and again at send time** in the worker
  (`apps/api/relayiq/services/ssrf.py`, `apps/api/relayiq/workers/tasks.py`).
- Concurrency-safe budgets (guarded UPDATE reservation), durable idempotency via DB
  unique constraints, bounded retries + circuit breakers.
- Structured logs with secret redaction and email masking
  (`apps/api/relayiq/logging_setup.py`).
- CI security scanning: pip-audit, gitleaks (full history), Trivy image scan
  (`.github/workflows/security.yml`).

The full analysis, threat by threat, is in `docs/security/threat-model.md`.

## Known limitations (read these before deploying)

Listed honestly; none of these are hidden behind marketing language.

1. **Development JWT auth, not OAuth.** Login is bcrypt-verified users + HS256 JWTs
   signed with a single shared secret (`RELAYIQ_JWT_SECRET`). This is a documented
   development substitute for a real OAuth/OIDC integration. With
   `RELAYIQ_ENV=production` the app **refuses to boot** on dev-placeholder or
   short (<32 char) secrets (`relayiq/config.py::validate_production_settings`).
   There is no token revocation list — deactivating a user takes effect on the next
   request (claims are re-checked against the DB), but a stolen *signing secret*
   mints valid tokens until rotated. Login attempts are rate-limited
   (default 5/min per client IP, Redis-backed).
2. **Webhook secrets are global by default; per-tenant scoping is opt-in.** A
   tenant that sets `tenant.settings["webhook_secrets"]` is verified ONLY against
   its own secrets — the global set no longer authorizes its deliveries
   (`api/routers/webhooks.py`; see docs/production-checklist.md §3). Tenants
   without their own secrets still share `RELAYIQ_WEBHOOK_SECRETS`, so multi-tenant
   production deployments should configure per-tenant secrets for every tenant.
3. **Simulated-provider rate limits are per-process.** The `CircuitBreaker` now
   shares state across processes via Redis (with in-process fallback if Redis is
   down), and API rate limiting is Redis-backed (`services/ratelimit.py`). What
   remains per-process is the *simulator's* `_SlidingWindowLimiter`, which models
   the upstream provider's own limit — a real provider adapter should implement its
   vendor's documented limits.
4. **No external security review.** This codebase has been internally reviewed and
   tested (325 passing tests including concurrency, webhook-security, and
   production-hardening suites) but has **not** undergone an independent security
   audit or penetration test.
5. **Providers are simulated.** All enrichment providers are deterministic simulators
   (ADR-009); the HubSpot CRM adapter is implemented and fixture-tested but live
   synchronization has not been verified. Handling of hostile *real-world* provider
   responses is therefore untested against live traffic.
6. **No Postgres row-level security.** Tenant isolation is application-level query
   discipline (ADR-010). A missed `tenant_id` filter would be a cross-tenant read.
7. **PII in Redis key names.** Field-cache keys embed lowercased emails/domains
   (ADR-003); anyone with Redis access can enumerate them.
8. **API rate limits are per-IP, not per-token.** Redis-backed limits protect
   login (5/min), webhooks (120/min) and the general API (600/min) per client IP
   (`RELAYIQ_RATE_LIMIT_*`); the limiter fails open on Redis outages by design.
   Per-token/tenant quotas beyond budgets are not implemented.
9. **Seeded demo credentials.** Local/dev seeding creates documented demo users
   with a published password. The seeder now **refuses to run** when
   `RELAYIQ_ENV=production` unless explicitly forced with per-role passwords
   supplied via the environment (`relayiq/seed/cli.py::_production_seed_guard`).

## Secrets handling

All secrets are supplied via environment variables (`apps/api/relayiq/config.py`).
Never commit `.env` files; gitleaks runs in CI against full history. CRM credentials
are read from the environment, not stored in database configuration rows.
