# ADR-005: Rules-based confidence scoring before any learned model

## Status

Accepted

## Date

2026-07-11

## Context

Acceptance, review routing, and CRM gating all need a confidence number per field and
per entity. A learned model needs labeled outcomes (reviewer decisions) that do not
exist on day one, and its scores are hard to explain to a reviewer staring at a
conflict. The first version must be transparent, deterministic, and honest about not
being a probability.

## Decision

Ship a **rules-based weighted-component score, version `rules-v1`**
(`relayiq/engines/confidence.py`), and measure its calibration instead of asserting it.

**Field score** = weighted mean of available components − conflict penalty, clamped
to [0, 1]. Weights (`WEIGHTS` in the module):

| component        | weight | source |
|------------------|--------|--------|
| prior            | 0.25   | 0.5·provider reliability prior + 0.5·field quality prior |
| freshness        | 0.20   | `exp(−ln2 · age_days / stale_days)` (`relayiq/services/staleness.py::freshness_factor`) |
| agreement        | 0.20   | (providers agreeing − 1) / (providers − 1), from reconciliation |
| format           | 0.10   | `validate_field(...)` pass/fail (`relayiq/canonical/normalize.py`) |
| consistency      | 0.10   | cross-field checks when applicable |
| provider_native  | 0.05   | provider-reported confidence when present |
| review_history   | 0.10   | 1 human-accepted / 0 human-rejected |

Weights of unavailable (None) components are redistributed by dividing by the sum of
available weights. Penalty = `0.25 × conflict_severity` (severity comes from the
reconciliation engine, ADR-006). Component values and the penalty are stored with
every score in `confidence_evaluations.components`, with
`formula_version = "rules-v1"`.

**Entity score** (`score_entity`) = weighted mean of field scores (required fields
weight 2.0) × fill-ratio adjustment (`0.6 + 0.4 × fill_ratio`) × identity match
certainty. **Sync score** (`score_sync`) = min(entity, min synced field) −
`min(0.3, 0.15 × unresolved_conflicts)`.

**Honesty mechanism**: the module docstring states it is "a heuristic *confidence
score*, NOT a calibrated probability". Calibration is measured separately by
`relayiq/benchmark/calibration.py`, which scores every accepted field against the
synthetic world's known truth and reports Brier score, ECE, and a 10-bucket
reliability table, embedding an `honest_interpretation` string in the report.

## Alternatives considered

- **Logistic regression / gradient boosting on review outcomes** — no labels at
  launch; planned as a successor once `review_decisions` accumulates ground truth.
  `formula_version` on `confidence_evaluations` exists so scores from different
  formulas are never silently mixed.
- **Provider-native confidence only** — providers self-grade generously and
  incomparably; used here as a 0.05-weight component, not the signal.
- **LLM-as-judge** — non-deterministic, unexplainable to reviewers, and adds a paid
  dependency in the hot path.

## Consequences

- Every threshold decision (auto-accept ≥ campaign `min_confidence`, CRM gate
  `min_confidence`, usable-lead minimum) is reproducible from stored components.
- Scores can be recomputed offline for what-if analysis because inputs are persisted
  (observations, priors, thresholds).
- The score is a *ranking* signal until calibration proves otherwise — the
  calibration report says exactly this when ECE ≥ 0.08.

## Risks

- Hand-set weights encode design intuition, not data; they will be wrong in specific
  regimes (e.g. a single fresh observation from a weak provider).
- Measured calibration is against **synthetic** truth; real-world calibration is
  unknown until real providers and reviewer labels exist.

## Revisit conditions

- Enough resolved review tasks to train and honestly evaluate a learned model.
- Measured ECE on real data materially worse than the synthetic baseline.
- New components (e.g. email deliverability signals) that don't fit the weighted-mean
  shape.
