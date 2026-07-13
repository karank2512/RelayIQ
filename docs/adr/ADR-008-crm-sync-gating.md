# ADR-008: CRM sync gating — every field earns its write

## Status

Accepted

## Date

2026-07-11

## Context

The most damaging failure mode of an enrichment pipeline is silently overwriting a
correct, recently-updated CRM value with a wrong or stale enrichment. A CRM write is
effectively irreversible in practice (downstream automations fire on it). Writes must
therefore be gated per field, with the reasoning recorded.

## Decision

A per-field decision gate (`relayiq/services/crm_gate.py::gate_field`) runs before any
CRM write, orchestrated by `relayiq/services/crm_sync.py::sync_entity`. Outcomes
(`SyncGateOutcome` in `relayiq/enums.py`):
`write | no_write | secondary_property | require_approval | preserve_crm | mark_refresh`.

Gate order, as implemented:

1. `no_write` — campaign disables CRM writes (`campaigns.crm_write_enabled`) or tenant
   policy blocks the field (`policy.crm_write_allowed`, driven by
   `tenant.settings["crm_write_blocked_fields"]`); no canonical value; reviewer
   rejected the value.
2. `preserve_crm` — field is manually locked by an operator
   (`canonical_field_values.locked`).
3. `require_approval` — unresolved provider conflict without reviewer sign-off, or
   confidence below the sync threshold (`min_confidence`, default 0.6). A reviewer
   decision of accepted/overridden/corrected **overrides the confidence threshold**
   ("human judgment wins").
4. `mark_refresh` — the canonical value is `expired`, or `stale` without reviewer
   approval: "don't push data we ourselves consider expired".
5. CRM comparison — when the CRM already holds a value:
   - equivalent value (`values_equivalent`, normalization-aware) → `no_write`;
   - CRM value **fresh** (aged via per-property timestamps against staleness
     thresholds) and enrichment confidence < 0.85 → `preserve_crm`;
   - CRM value fresh but enrichment confidence ≥ 0.85 → `secondary_property`: the
     value is written to a `relayiq_suggested_*` property for operator comparison,
     never over the primary;
   - CRM value aging/stale → overwrite is allowed and the reason recorded.
6. `write` — passed confidence, conflict, staleness, and CRM-comparison checks.

Supporting mechanisms:

- The reconciliation engine already refuses to fight the CRM without evidence: a
  fresh CRM value that no provider corroborates yields outcome `RETAIN_CRM`
  (`relayiq/engines/reconciliation.py`, comment cites ADR-008).
- Every field's `{before, after, gate, reasons}` and the whole gate summary are
  persisted on `crm_sync_attempts.field_changes` / `gate_summary`; gate outcomes are
  counted in `relayiq_crm_gate_total{outcome}`.
- Sync attempts are idempotent per value-set (ADR-07) and audited
  (`relayiq/services/audit.py` record with action `crm.sync`).
- Dry-run is first-class: `dry_run` jobs or a `dry_run`-mode connection record what
  *would* be written (`gate_summary.would_write`) with status `skipped`.
- Adapters: the built-in simulator writes to `crm_sim_records` (inspectable "CRM");
  a HubSpot v3 adapter exists and is unit-tested against recorded shapes but has
  **not** been verified live (`relayiq/services/crm.py` module docstring).

## Alternatives considered

- **Record-level gating** (sync all-or-nothing) — one low-confidence field would
  block nine good ones, or worse, ride along with them.
- **Always-write with CRM history as the undo** — CRM property history is not a
  reliable rollback across systems, and downstream automations fire regardless.
- **Only a confidence threshold** — misses the cases that actually burn users:
  fresh CRM values, manual locks, unresolved conflicts, staleness.

## Consequences

- A wrong-but-confident enrichment against a fresh CRM value lands in a secondary
  property instead of destroying data; a human adjudicates.
- The gate trail makes "why didn't this sync?" answerable from the attempt row alone.
- More reviewer work by design: `require_approval` routes to the review queue rather
  than guessing.

## Risks

- The 0.85 secondary-property threshold and 0.6 default `min_confidence` are policy
  constants, not learned values.
- Per-property CRM timestamps come from the simulator's own store; real CRMs expose
  this unevenly (HubSpot adapter currently returns an empty
  `property_updated_at` map, so CRM-freshness checks degrade to `unknown` age there).

## Revisit conditions

- Live HubSpot verification (would firm up property-timestamp handling).
- Reviewer telemetry showing `secondary_property` suggestions are almost always
  accepted (threshold could relax) or almost always rejected (tighten).
