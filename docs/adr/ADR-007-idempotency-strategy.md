# ADR-007: Idempotency strategy

## Status

Accepted

## Date

2026-07-11

## Context

Duplicate requests are the norm, not the exception: Clay retries HTTP calls, webhook
providers redeliver, Celery redelivers with `acks_late`, users double-click. Every
duplicate that slips through spends provider credits twice and can double-write the
CRM. Redis-based locks are insufficient because the guarantee must survive Redis
loss and worker crashes.

## Decision

**Durable, DB-backed claims** plus idempotent re-entry at every layer:

1. **API idempotency** (`relayiq/services/idempotency.py`): a claim inserts an
   `idempotency_records` row protected by `unique(tenant_id, scope, key)`
   (`ix_idem_unique`). The docstring calls this "the only mechanism that is safe
   under concurrent identical requests, worker restarts, and retries". Outcomes:
   - `NEW` → caller proceeds, must later `complete()` (stores a
     `response_snapshot`) or `fail()`.
   - `IN_PROGRESS` → concurrent duplicate → HTTP 409
     (`relayiq/api/routers/enrichment.py`).
   - `COMPLETED` → replay: the stored snapshot is returned, nothing is spent.
   - `MISMATCH` → same key, different `request_hash` (SHA-256 of the canonicalized
     body) → HTTP 422.
   - Expired records (TTL 48 h, `Settings.idempotency_ttl_hours`) and `FAILED`
     records are re-claimed in place.
   Scopes in use: `enrichment` (per-row execute; key from the `Idempotency-Key`
   header or body field), `enrichment_batch`.
2. **Webhooks**: delivery dedup by `unique(tenant, source, delivery_id)` on
   `webhook_deliveries`; a duplicate insert raises `IntegrityError` and the handler
   returns the original `job_id` with `duplicate: true` and does **not** create a
   second job (`relayiq/api/routers/webhooks.py`). Jobs created from webhooks get
   `idempotency_key = "webhook:{delivery_id}"`.
3. **Job execution**: `run_enrichment_job` is an idempotent re-entry — jobs that have
   left `received`/`queued` return their stored `result_summary`
   (`relayiq/engines/orchestrator.py`), making Celery redelivery safe. A pre-decision
   check additionally skips a new job when another job for the same entity is already
   `running` (`relayiq/engines/decision.py`, step 5 — catches concurrent
   different-key submissions).
4. **CRM sync**: attempts are keyed by a **value fingerprint** —
   `{entity_type}:{entity_id}:{sha256(field→value map)[:24]}` — stored in
   `crm_sync_attempts.idempotency_key` with a unique index (`ix_sync_idem`); an
   identical value-set that already synced returns the prior attempt instead of
   writing twice (`relayiq/services/crm_sync.py`).
5. **Review actions**: optional `Idempotency-Key` replays the recorded
   `review_decisions` row (`relayiq/services/review.py::apply_action`).

## Alternatives considered

- **Redis SETNX locks** — lost on Redis restart; cannot serve response replays after
  completion. Redis locks are used only for the cache-refresh stampede lock
  (ADR-003), where loss is harmless.
- **Request-hash-only dedup (no client key)** — legitimate identical resubmissions
  (same row re-sent intentionally) would be indistinguishable from retries; the
  explicit key gives clients control, with the hash as a mismatch guard.
- **Exactly-once via distributed transactions** — not attainable across HTTP
  providers; at-least-once with idempotent effects is the honest contract.

## Consequences

- Replays are cheap and observable: a replayed execute returns the recorded
  `JobOut` snapshot; a replayed webhook returns `duplicate: true`.
- The claim row is committed *before* the work; a crash after claim but before
  completion leaves `IN_PROGRESS` until TTL expiry — callers get 409 during that
  window (documented behavior, favors "never double-spend" over availability).
- Expired-claim re-claim is a plain UPDATE ("race-safe enough for our TTLs" — module
  comment): two processes racing an expired record could both proceed; the losing
  side's effects are still bounded by job-level and sync-level idempotency.

## Risks

- Stuck `IN_PROGRESS` claims after a crash block retries for up to 48 h unless
  manually cleared.
- Idempotency does not cover mid-job provider-call crashes (see ADR-001) — those are
  bounded, visible in the ledger, but possible.

## Revisit conditions

- Need for automatic reaping of stuck `IN_PROGRESS` claims (heartbeat/lease model).
- Multi-region deployment (unique-constraint claims assume one primary database).
