# ADR-003: Redis field-cache key layout and TTL semantics

## Status

Accepted

## Date

2026-07-11

## Context

The single biggest waste in enrichment spend is re-buying a field that was already
bought. RelayIQ needs a hot-path cache in front of the canonical store that is
tenant-safe, versionable, staleness-aware, and able to remember *negative* results
(provider had nothing) so known-empty lookups are not re-purchased.

## Decision

Implemented in `relayiq/services/cache.py` (`FieldCache`).

**Key layout** (tenant- and schema-version-scoped):

```
riq:{schema_version}:{tenant_id}:f:{entity_type}:{entity_key}:{field}     # positive entry
riq:{schema_version}:{tenant_id}:neg:{entity_type}:{entity_key}:{field}   # negative entry
riq:{schema_version}:lock:{tenant_id}:{entity_type}:{entity_key}:{field}  # refresh lock
```

- `schema_version` comes from `Settings.cache_schema_version` (`"v1"` in
  `relayiq/config.py`) — bumping it orphans all old entries instantly (no migration
  of cached data, no poisoned-format reads).
- `entity_key` is the natural key (`work_email` for contacts, `root_domain` for
  accounts — `relayiq/services/entities.py::entity_lookup_key`), lowercased in the
  key builders.

**Two-tier TTL (stale-while-revalidate)**: each positive entry stores a
`soft_expiry_epoch` in its JSON document; the Redis `EX` TTL is the hard bound.
`get_field` returns `HIT` before soft expiry and `STALE_HIT` after it (the caller may
serve and refresh); past the hard TTL the key is simply gone (`MISS`). The
orchestrator writes accepted values with staleness-policy-derived TTLs
(`relayiq/engines/orchestrator.py`, reconciliation step):

- hard TTL = `thresholds.stale_days * 86400`
- soft TTL = `thresholds.fresh_days * 86400`

Defaults when not supplied (`relayiq/config.py`): hard TTL 6 h
(`cache_default_ttl_seconds = 21600`), soft = half the hard TTL, negative TTL 15 min
(`cache_negative_ttl_seconds = 900`), lock TTL 10 s (`cache_lock_ttl_seconds`).

**Negative caching**: after the provider-call step, fields no provider could fill are
written via `set_negative` (`orchestrator.py`: "avoid re-buying known-empty lookups");
`get_field` reports them as `NEGATIVE_HIT`.

**Stampede protection**: `acquire_refresh_lock` is `SET NX EX` returning a random
token; `release_refresh_lock` is a Lua compare-and-delete so one worker can never
release another's lock.

**Safety details**: corrupt JSON is treated as a miss (`corrupt_miss` counter);
writes pipeline the positive `SET` with a `DELETE` of the matching negative key;
`invalidate_entity` uses `SCAN` (never `KEYS`); all operations increment
`relayiq_cache_ops_total{entity_type,field,status}` with bounded label cardinality.

## Alternatives considered

- **Cache the whole entity as one blob** — one stale field would invalidate the whole
  record; field-level keys match field-level routing and staleness policies.
- **Postgres-only (no Redis)** — `canonical_field_values` is already consulted in the
  pre-decision step (`relayiq/engines/decision.py::_fresh_canonical_fields`); Redis
  adds the sub-millisecond path and negative entries without extra rows.
- **Redis hashes per entity** — no per-field TTLs, which the staleness model requires.
- **Encrypt/hash keys** — keys embed emails/domains; acceptable in the current dev
  posture, noted as a residual risk in the threat model.

## Consequences

- Cross-provider dedup happens naturally: whoever populated the value, the next
  request for it is free, and the ledger records the avoided cost
  (`avoided_cost_credits` on the cache-hit entries written in
  `orchestrator.py`).
- Cache invalidation on review actions is explicit: reviewer overrides re-write the
  entry, reversals call `invalidate_field` (`relayiq/services/review.py`).
- Tenant isolation in Redis is by key prefix only — code discipline, not an ACL
  (see ADR-010).

## Risks

- Entity keys contain PII (emails) in plaintext key names; anyone with Redis access
  can enumerate them.
- `STALE_HIT` background refresh is available (statuses + lock exist) but the
  orchestrator currently only *serves* fresh hits; stale-hit-triggered refresh is not
  wired to a background task.

## Revisit conditions

- Multi-node Redis (cluster) — key layout already hash-tags nothing; review slot
  distribution.
- Compliance requirements for PII in key material (would force hashed entity keys).
- Implementing background refresh on `STALE_HIT`.
