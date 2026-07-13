# ADR-012: Raw payload retention policy

## Status

Accepted

## Date

2026-07-13

## Context

Two kinds of "raw" data flow through RelayIQ: provider responses and inbound webhook
bodies. Retaining them helps debugging and lineage ("why did we accept this value?"),
but raw payloads are also where liability concentrates: provider responses may contain
licensed data and PII beyond the fields that were requested, and webhook bodies contain
whatever the sender put in them. Storage policy has to be decided per class of data,
not by default-keeping everything.

## Decision

**Provider responses: retained, because in this build they are synthetic.**

`relayiq/models/providers.py::ProviderResponse` stores `raw_payload` and
`normalized_payload` (JSONB) keyed to a `ProviderRequest`. The write path
(`relayiq/services/provider_exec.py::execute_with_retries`) persists what the adapter
returned in `EnrichmentCallResult.raw_payload`. Today every adapter is a simulator
(ADR-009), so the "raw payload" is a small synthetic summary —
`{"match": bool, "fields": {...}}` built in
`relayiq/providers/simulators.py::SimulatedProvider.enrich` — containing only the
requested fields drawn from the synthetic world. It is safe to retain indefinitely and
valuable for lineage (each `FieldObservation` links back through
`provider_request_id`).

**Rule for real providers (binding on any live adapter):** full raw payloads from a
real vendor MUST NOT be retained indefinitely in `provider_responses`. A live adapter
must either (a) store only the **normalized summary** (the fields RelayIQ requested,
post-`relayiq/canonical/normalize.py`) in `normalized_payload` and leave
`raw_payload` empty, or (b) store the full raw payload **TTL'd** (deleted after a
short debugging window) or **encrypted at rest with restricted access**. Rationale:
vendor contracts commonly restrict caching/retention of licensed records, and raw
responses routinely carry fields the tenant never asked for (extra emails, phones,
socials) — retaining them silently expands the PII surface far beyond what the
canonical store needs. The columns are already nullable to support this.

**Webhook payloads: NOT retained.** `relayiq/models/webhooks.py::WebhookDelivery`
stores only a **SHA-256 hash** of the raw body (`payload_hash`) plus minimal event
metadata (`event_meta = {"event", "entity_type"}` — see the router,
`relayiq/api/routers/webhooks.py`). The hash is enough to prove what was received and
to detect divergent bodies reusing a delivery id; the useful *content* of the payload
is immediately turned into first-class rows (entity, `EnrichmentJob`) and the body is
dropped. Nothing sensitive a sender pushes at us sits in a blob column.

## Alternatives considered

- **Retain everything forever (default ORM behavior)** — cheapest to build, and what
  most pipelines accidentally do; rejected for the contractual/PII reasons above.
- **Retain nothing, ever** — makes provider-side bugs ("vendor returned garbage last
  Tuesday") undiagnosable and breaks observation lineage; rejected while the payloads
  are synthetic and tiny.
- **Ship raw payloads to object storage (S3) with lifecycle rules** — the right shape
  at real volume; overkill for a single-Postgres pilot. Compatible with the TTL rule
  above; noted for revisit.
- **Store webhook bodies encrypted for N days** — dedup + audit only need the hash;
  keeping bodies at all invites using them, so we keep the stricter posture.

## Consequences

- Lineage queries (observation → provider_request → provider_response) work today and
  in a live deployment the same query returns the normalized summary instead of the
  vendor blob.
- Webhook disputes can be settled by hash comparison but not by payload inspection —
  an accepted trade; the derived rows (job, entity, audit events) carry the semantic
  content.
- `provider_responses` has no TTL machinery yet because none is needed for synthetic
  data; a live adapter PR must bring the TTL/encryption implementation with it (this
  ADR is the gate).

## Risks

- The retention rule for real providers is **policy, not yet code** — nothing today
  technically prevents a future adapter from writing full vendor payloads into
  `raw_payload`. Enforcement is review discipline plus this ADR.
- `payload_hash` proves integrity only if the raw body was captured exactly;
  any middleware that rewrites bodies would break hash comparability.
- Simulator payloads embed synthetic emails/domains; harmless now, but the pattern
  would be PII if the world file were ever swapped for real data (the seed pipeline
  generates `.test` domains and invented names precisely to avoid this).

## Revisit conditions

- First live provider adapter → implement TTL'd or encrypted `raw_payload` handling
  (and pick per-vendor windows to match contract terms).
- Volume: if `provider_responses` becomes a top-3 table by size, move payloads to
  object storage with lifecycle rules.
- Any compliance regime (GDPR deletion requests) → payload deletion must be reachable
  from tenant/entity deletion paths.
