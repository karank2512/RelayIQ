# ADR-006: Store observations; never overwrite them

## Status

Accepted

## Date

2026-07-11

## Context

When two providers disagree about a job title, a system that keeps only "the value"
destroys the evidence needed to reconcile, review, reverse, and measure provider
quality. Enrichment data is inherently multi-source and time-varying; the storage
model has to preserve that.

## Decision

Separate **evidence** from **selection**:

- **Evidence**: `field_observations` (`relayiq/models/observations.py`) — one
  append-only row per provider-returned field value, carrying provenance
  (`provider_key`, `provider_request_id`), freshness (`source_timestamp`,
  `retrieved_at`, `staleness_state`), cost (`cost_credits`), validation results, and
  confidence fields. The module docstring: "Never overwritten (ADR-006)". Selection
  state is expressed with flags (`is_selected`, `is_rejected`, `rejection_reason`,
  `review_status`) — rows are flagged, never mutated in value or deleted.
- **Selection**: `canonical_field_values` (`relayiq/models/entities.py`) — exactly one
  row per (tenant, entity, field) (`ix_cfv_unique`), pointing back at
  `selected_observation_id` and `reconciliation_decision_id`. It is the *pointer* to
  the winning evidence, plus confidence/staleness metadata and a `locked` flag that
  blocks any automatic overwrite (`relayiq/services/entities.py::upsert_canonical_value`
  returns early when `row.locked`).
- **Decisions**: `reconciliation_decisions` records each adjudication — outcome,
  chosen observation, all `observation_ids` considered, human-readable `reasoning`,
  factor breakdown, and `conflict_severity`. The reconciliation engine
  (`relayiq/engines/reconciliation.py`) "never deletes anything — selection state
  lives on the observations, the decision in reconciliation_decisions".
- **Reversibility**: review actions snapshot prior canonical state into
  `review_decisions.previous_state` before changing anything
  (`relayiq/services/review.py::_snapshot`), so `REVERSE` restores it losslessly, and
  the reversal is itself a new appended decision.
- Entity columns (`contacts.job_title`, …) are a **projection** of accepted canonical
  values for list views and CRM mapping
  (`relayiq/services/entities.py::apply_canonical_to_entity`), not a store of record.

Reconciliation on each job runs over **all historical observations** for the field,
not just today's batch (`orchestrator.py` reconciliation step queries every
`FieldObservation` for the tenant/entity/field).

## Alternatives considered

- **Overwrite-in-place with an audit log** — audit logs record that a change happened
  but not the full competing evidence set; provider precision
  (`relayiq/services/quality.py::provider_field_quality` — selected/rejected shares
  per provider×field) would be unmeasurable.
- **Keep only latest observation per provider** — breaks freshness-weighted
  reconciliation and the dynamic routing strategy, both of which read observation
  history.
- **Event sourcing of entities** — heavier than needed; the observation/selection
  split gives replayability for the one domain where it pays.

## Consequences

- Full lineage per field is reconstructable: input → routing → provider request →
  observation → reconciliation → confidence → review → CRM sync
  (`relayiq/services/lineage.py::field_lineage` walks exactly this chain).
- Reviewer UI can show every candidate with cost/age/provider, because they all still
  exist.
- Storage grows monotonically with enrichment volume; there is **no pruning job** for
  old observations in this build.

## Risks

- Unbounded observation growth (cost and index bloat) — acceptable at MVP volume,
  needs partitioning/archival later.
- Reconciling over all history means a bad old observation keeps participating until
  it is flagged rejected or ages out via freshness weighting.

## Revisit conditions

- Observation tables reaching sizes where reconciliation queries degrade.
- Data-retention obligations forcing deletion (would need tombstoning that preserves
  decision integrity).
