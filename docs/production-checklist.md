# Production deployment checklist

RelayIQ **fails fast** when `RELAYIQ_ENV=production` is set with unsafe configuration —
the API and worker refuse to boot rather than run with development defaults. This page is
the complete list of what you must set and what the app enforces.

## 1. Required environment (enforced at startup)

| Variable | Requirement | Enforced |
|---|---|---|
| `RELAYIQ_ENV` | `production` | activates all checks below |
| `RELAYIQ_JWT_SECRET` | ≥32 non-whitespace chars, ≥8 distinct, not a dev placeholder | ✅ boot refusal |
| `RELAYIQ_WEBHOOK_SECRETS` | ≥1 secret, each ≥32 chars / ≥8 distinct, no dev placeholders | ✅ boot refusal |
| `DATABASE_URL` | must not contain the dev password | ✅ boot refusal |
| `RELAYIQ_CORS_ORIGINS` | your dashboard's public origin(s); no `*`, no localhost | ✅ boot refusal |
| `RELAYIQ_METRICS_TOKEN` | set (or `RELAYIQ_METRICS_ENABLED=false`) | ✅ boot refusal |
| `REDIS_URL` / `CELERY_*` | point at managed Redis | not validated — verify via `/readyz` |

Generate secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 2. What the app enforces at runtime (no action needed)

- **Rate limiting** (Redis-backed, shared across processes): login 5/min/IP, webhooks
  120/min/IP, other API 600/min/IP — tune via `RELAYIQ_RATE_LIMIT_*`. Fixed-window, so a
  burst straddling a window boundary can briefly reach ~2× the limit; that is acceptable
  for abuse control. Fails **open** on a Redis outage (logged + counted in
  `relayiq_rate_limited_total`) — an attacker who can pressure Redis removes rate limiting,
  so alert on the counter.
- **Security headers** on every response (nosniff, frame-deny, HSTS, no-referrer,
  no-store) and a request-body cap enforced at the ASGI layer (`RELAYIQ_MAX_BODY_BYTES`,
  default 2 MB) — a chunked transfer or a missing/forged `Content-Length` cannot bypass it.
- **Shared circuit breakers**: provider failure state lives in Redis, so one worker's
  failures protect all processes. Half-open admits a single probe per cooldown window
  (no re-flooding a still-down provider), and the in-process mirror keeps the breaker
  open through a Redis outage rather than disarming.
- **SSRF protection** on callback URLs blocks private networks in production
  (`allow_private` is only enabled outside production) and re-validates at send time.
- **Webhook security**: HMAC (constant-time) + replay window + delivery-ID dedup.
- **Seeding guard**: `relayiq.seed.cli` refuses to create demo data in production unless
  `RELAYIQ_SEED_ALLOW_PRODUCTION=1` **and** every `RELAYIQ_SEED_*_PASSWORD` is set.

## 3. Per-tenant webhook secrets (recommended for multi-tenant deployments)

By default one global secret authenticates webhooks and the payload's `tenant_slug`
selects the tenant. To scope authorization per tenant, set on the tenant row:

```sql
UPDATE tenants SET settings = settings || '{"webhook_secrets": ["<tenant-secret>"]}'
WHERE slug = 'acme';
```

Once the `webhook_secrets` key is present, **only** that tenant's secrets verify its
deliveries — the global secret is no longer accepted for it. This is fail-closed: an
empty or malformed list rejects ALL of that tenant's deliveries (it never silently
falls back to the global secret). Rotate by listing new + old, then removing the old;
to return a tenant to the global secret, remove the key entirely.

## 4. Deployment shape

- Run the API behind TLS termination (Fly.io/ALB/nginx). Start uvicorn with
  `--proxy-headers --forwarded-allow-ips=<proxy>` AND set `RELAYIQ_TRUST_FORWARDED_FOR=1`
  so rate limiting keys on the real client IP (the left-most `X-Forwarded-For` hop). Leave
  it **unset** when NOT behind a trusted proxy — otherwise clients spoof their IP to evade
  limits. Without it, all clients behind the proxy share one rate-limit bucket.
- Run migrations as a release step (`alembic upgrade head`), **not** the seed command.
- Do **not** reuse `docker-compose.yml` as-is in production — it is the development
  stack (it seeds demo data and publishes every port). See `docs/deployment.md` for the
  Fly.io path and `infrastructure/terraform/`.
- Keep `/metrics` reachable only by your Prometheus (network policy) **and** set
  `RELAYIQ_METRICS_TOKEN` (defense in depth).
- Set `RELAYIQ_EXPOSE_DOCS=false` if you don't want `/docs` public (auth still protects
  every API endpoint either way).

## 5. Verify after deploy

```bash
curl -fsS https://api.example.com/healthz          # {"status":"ok"}
curl -fsS https://api.example.com/readyz           # database + redis "ok"
curl -s -o /dev/null -w '%{http_code}\n' https://api.example.com/metrics   # 401 without token
# 6 rapid bad logins from one IP → the last ones must be 429
```

## 6. Honest limitations that remain (see SECURITY.md)

- Authentication is JWT + seeded users — a documented OAuth/SSO substitute. Wire your IdP
  before exposing this to an organization.
- Enrichment providers are simulators; live Clay/HubSpot paths are implemented but not
  verified against live accounts.
- No external penetration test or security review has been performed.
- JWTs are stateless with no revocation list; keep `RELAYIQ_JWT_TTL_SECONDS` short.
