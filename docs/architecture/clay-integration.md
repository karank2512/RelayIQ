# Clay integration — sidecar contract mapping

> **Status: contract implemented, NOT live-tested.** RelayIQ's enrichment endpoints
> (`apps/api/relayiq/api/routers/enrichment.py`) implement a sidecar contract shaped
> for Clay's generic **"HTTP API" enrichment column** (POST with JSON body + custom
> headers). No request from an actual Clay workspace has been made against this
> build. Everything below the mapping section is an explicit assumption list, not a
> verified behavior.

## Why a sidecar, not a native integration

Clay lets a table column call an arbitrary HTTP API per row and map response JSON
back into columns. RelayIQ exposes exactly that shape: one POST per row, idempotent,
with a JSON response whose fields are stable and documented
(`relayiq/schemas/enrichment.py`). Clay keeps orchestrating the table; RelayIQ
decides whether/what to spend and returns governed values.

## Column configuration mapping

**Endpoint:** `POST {RELAYIQ_BASE_URL}/v1/enrichment/execute`

**Headers:**

| Header | Value | Purpose |
|---|---|---|
| `Authorization` | `Bearer <RelayIQ JWT>` | Auth; operator role required (`api/deps.py::require_operator`) |
| `Content-Type` | `application/json` | — |
| `Idempotency-Key` | a per-row stable key, e.g. `clay-{table_id}-{row_id}-{run_id}` | Re-runs of the table replay the recorded response and spend **nothing** (`services/idempotency.py`, ADR-007). Can also be supplied in the body as `idempotency_key`. |

**Body** (`EnrichmentRequestIn`) — Clay column references interpolated into a JSON
template:

```json
{
  "entity_type": "contact",
  "entity": {
    "work_email": "{{email}}",
    "full_name": "{{name}}",
    "company_domain": "{{domain}}"
  },
  "requested_fields": ["job_title", "seniority", "department", "linkedin_url"],
  "campaign_id": "<optional RelayIQ campaign id>",
  "idempotency_key": "clay-{{row_id}}",
  "metadata": {"clay_row": "{{row_id}}"},
  "mode": "sync",
  "dry_run": false
}
```

Validation is strict: `requested_fields` must come from the known contact/account
field lists (unknown fields → 422), `metadata` is capped at 8 KB, and identifier
fields have length caps. `callback_url` is available for async mode and is
SSRF-validated at intake and again at send time.

**Response** (`JobOut`) — fields Clay can map back into columns:

| Response field | Meaning |
|---|---|
| `id`, `status` | Job id; `completed`, `awaiting_review`, `rejected`, `skipped`, `blocked_budget`, ... |
| `pre_decision`, `decision_reasons` | Why RelayIQ did or didn't spend (e.g. suppressed, fresh-in-cache, budget) |
| `result_summary.fields_filled` | Count of delivered fields |
| `result_summary.served_from_cache` | Fields that cost nothing this run |
| `result_summary.entity_confidence` | rules-v1 score (a **ranking signal**, not a probability — measured ECE 0.0905) |
| `result_summary.accepted` / `review_required` | Whether values auto-accepted or went to human review |
| `result_summary.usable_lead`, `usable_lead_failures` | Usable-lead verdict + failed criteria |
| `estimated_cost_credits`, `actual_cost_credits` | Spend accounting per row |
| `trace_id` | Correlates to RelayIQ logs/traces |

Note that enriched **values** land in RelayIQ's canonical store and CRM (through the
sync gate) — the sidecar response reports decisions and quality, and values can be
fetched via `GET /v1/contacts/...` if a values-in-Clay flow is wanted.

**Dry-run / cost preview:** `POST /v1/enrichment/decide` with the same body returns
`{decision, reasons, fields_to_enrich, fields_from_cache, estimated_cost_credits,
budget_warning}` and spends nothing — usable as a first Clay column to skip rows
RelayIQ would reject.

**Batch alternative:** `POST /v1/enrichment/batch` (≤500 rows, async, one job per
row under a `batch_id`).

## Webhook alternative (push instead of column)

A Clay HTTP-API column can also target `POST /v1/webhooks/enrichment` with the
HMAC-signed contract (`X-RelayIQ-Signature: t=...,v1=...` over the raw body +
`X-Delivery-Id`, body per `WebhookEnrichmentPayload` including `tenant_slug`). This
requires the sender to compute HMAC-SHA256 — see the assumptions below — and enqueues
async jobs; duplicates return `duplicate: true` at zero spend (ADR-011).

## Assumptions (unverified against live Clay)

1. Clay's HTTP API column can send **custom headers with per-row templated values**
   (needed for `Idempotency-Key`). If headers can't be templated, the body's
   `idempotency_key` field carries the same behavior — this fallback is implemented.
2. Clay retries failed/timed-out calls. Assumed absorbed by idempotency: a replay
   returns the recorded response (`ClaimOutcome.COMPLETED`); a concurrent duplicate
   returns 409 (`IN_PROGRESS`) — **whether Clay treats 409 as retry-later is
   unverified.**
3. Clay's column timeout is long enough for `mode: "sync"` — measured p50 120 ms /
   p95 880 ms for full-pipeline sync on a dev laptop
   (`docs/benchmarks/load-test-results.md`), but Clay-side limits are unknown; if
   they bite, `mode: "async"` + polling `GET /v1/enrichment/jobs/{id}` (or
   `callback_url`) is the fallback.
4. Clay can map nested response JSON (`result_summary.*`) into columns. If only
   top-level keys are mappable, a flattened response view would be a small additive
   change.
5. Auth uses a long-lived operator token. RelayIQ dev JWTs default to an **8-hour
   TTL** (`jwt_ttl_seconds = 28800`, `relayiq/config.py`) — a static Clay header
   would expire. A live integration needs long-lived API keys or a token-refresh
   step; **not built**.
6. For the webhook path: Clay (or an intermediary) can compute the Stripe-style
   HMAC signature. If not, the REST sidecar path (bearer + idempotency) is the
   supported route.
7. Rate behavior: RelayIQ has no per-client API rate limiting (SECURITY.md §8);
   Clay's per-column concurrency is assumed modest. Sustained measured throughput on
   a dev laptop was 35.4 req/s — not a production capacity claim.

Each assumption is testable in under an hour with a real Clay workspace; none has
been tested in this build.
