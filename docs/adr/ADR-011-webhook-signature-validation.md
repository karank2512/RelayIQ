# ADR-011: Stripe-style HMAC webhook signature validation

## Status

Accepted

## Date

2026-07-13

## Context

RelayIQ's webhook endpoint (`POST /v1/webhooks/enrichment`) triggers paid work: an
accepted delivery creates an enrichment job that spends provider credits. Forged,
replayed, or duplicated deliveries are therefore not just an integrity problem but a
direct budget-exhaustion vector. The verification scheme must survive secret rotation
without downtime and must never leak timing information about which secret or
signature candidate matched.

## Decision

Implemented in `relayiq/services/webhook_security.py` (pure functions, no side
effects, never raises) and enforced in `relayiq/api/routers/webhooks.py`.

**Signature scheme (Stripe-style):**

- Header `X-RelayIQ-Signature: t=<unix_ts>,v1=<hex>` — multiple `v1` entries allowed,
  unknown keys ignored (`parse_signature_header` is tolerant and never raises).
- Signed payload is `b"{timestamp}." + raw_body`; the signature is the hex
  HMAC-SHA256 digest keyed with the utf-8 secret (`sign_payload`). Signing the
  timestamp into the payload binds the replay window to the signature — an attacker
  cannot refresh `t` on a captured body.
- Verification runs against the **raw request body** (`await request.body()`),
  before any JSON parsing — a signature over re-serialized JSON would be malleable.

**Constant-time, rotation-aware comparison:** `verify_webhook` tries **every**
configured secret against **every** `v1` candidate using `hmac.compare_digest`,
without short-circuiting, and reports only ok/not-ok — timing reveals neither which
secret nor which candidate matched. Secrets come from the comma-separated
`RELAYIQ_WEBHOOK_SECRETS` env var (`relayiq/config.py::webhook_secret_list`, newest
first), so rotation is: add the new secret, migrate senders, drop the old one — no
downtime, no dual endpoints.

**Replay window:** timestamps older than `replay_window_seconds` (default 300 s,
configurable via `RELAYIQ_WEBHOOK_REPLAY_WINDOW_SECONDS`) are rejected as
`stale_timestamp`; timestamps more than 60 s in the future
(`FUTURE_TOLERANCE_SECONDS`) as `future_timestamp`.

**Delivery-ID dedup (exactly-once spend):** every request must carry
`X-Delivery-Id`. The router inserts a `WebhookDelivery` row and relies on the DB
unique constraint `(tenant_id, source, delivery_id)`
(`relayiq/models/webhooks.py::ix_webhook_delivery_unique`); an `IntegrityError` means
a duplicate — the endpoint returns `200 {accepted: true, duplicate: true, job_id:
<original>}` and creates **no second job**. The unique constraint, not application
logic, is what makes this safe under concurrent duplicate deliveries. The job created
from a delivery also gets `idempotency_key = "webhook:{delivery_id}"`.

**Order of operations** (fail closed, fail cheap): verify signature on raw body →
check replay window → require delivery id → dedup insert → *only then* parse the
payload (Pydantic `WebhookEnrichmentPayload`) and resolve the tenant by slug.
Rejections log only the failure class (`reason=...`) — never the signature, secrets,
or payload — and increment `relayiq_webhooks_total{source,result}`.

Outbound callbacks reuse the same scheme: `build_signature_header` signs worker
callback deliveries (`relayiq/workers/tasks.py::_maybe_callback`) so receivers can
authenticate RelayIQ symmetrically.

## Alternatives considered

- **Static bearer token in a header** — no replay protection, no rotation story,
  token equals capability forever if logged/leaked in transit middleware.
- **mTLS** — strongest transport-level option but unrealistic to demand from
  Clay-style SaaS senders; operationally heavy for a pilot.
- **JWT-signed payloads** — adds a parser (and its CVE surface) to the *pre-auth*
  path; HMAC over raw bytes keeps the unauthenticated surface to `hmac` + string
  splitting.
- **IP allowlisting** — sender IPs are cloud-dynamic; provides no payload integrity.
- **Timestamp check without signing `t` into the payload** — would let an attacker
  re-send an old signed body with a fresh header timestamp; rejected.

## Consequences

- Replays inside the 300 s window with the **same** delivery id are absorbed at zero
  spend (covered by unit tests `tests/unit/test_webhook_security.py` and the e2e
  duplicate-delivery scenario).
- Senders must be able to set two custom headers and compute HMAC-SHA256 — true of
  Stripe-compatible webhook frameworks; a documented assumption for Clay
  (docs/architecture/clay-integration.md).
- Clock skew beyond +60 s / −300 s between sender and RelayIQ rejects valid traffic;
  the window is configurable per deployment.

## Risks

- Secrets are **global**, and the tenant is resolved from the payload's
  `tenant_slug` — any valid signer can enqueue enrichment for any tenant (documented
  in SECURITY.md and threat model §1). Per-tenant secrets are the fix.
- A signer who holds the secret can generate unlimited *distinct* delivery ids;
  dedup does not rate-limit a compromised secret — budgets (`relayiq/services/budget.py`)
  are the backstop.
- `webhook_deliveries` rows accrue indefinitely (dedup horizon = table lifetime);
  no TTL/pruning job exists yet.

## Revisit conditions

- Per-tenant webhook secrets (changes the router's secret lookup, not the verifier).
- A second inbound source (`source` column already discriminates) with different
  header conventions.
- Pruning policy for `webhook_deliveries` once volume makes the table a cost.
