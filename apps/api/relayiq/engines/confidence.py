"""Rules-based confidence model, version `rules-v1` (ADR-005).

This is a heuristic *confidence score*, NOT a calibrated probability — calibration is
measured separately (relayiq/benchmark/calibration.py) and reported honestly.

Field-level score = weighted mean of available components, minus a conflict penalty,
clamped to [0, 1]:

    components (weight):
      prior        (0.25)  0.5*provider reliability prior + 0.5*field-level quality prior
      freshness    (0.20)  exp(-ln2 * age_days / stale_days)   (services/staleness.py)
      agreement    (0.20)  (#providers agreeing with value - 1) / (n_providers - 1)
      format       (0.10)  1 if validate_field(...) passes else 0
      consistency  (0.10)  cross-field checks (email↔domain, count↔range, title↔seniority)
      provider     (0.05)  provider-native confidence when reported
      review       (0.10)  1 human-accepted / 0 human-rejected history for this value

    Weights of unavailable components are redistributed (weighted mean over available).
    penalty = 0.25 * conflict_severity  (severity from the reconciliation engine)

Entity-level = weighted mean of field scores (required fields weight 2.0, others 1.0)
               * fill_ratio_adjustment * identity_match_certainty
Sync-level   = min(entity, min over synced fields) with unresolved-conflict penalty.
"""

from dataclasses import dataclass, field as dc_field

FORMULA_VERSION = "rules-v1"

WEIGHTS = {
    "prior": 0.25,
    "freshness": 0.20,
    "agreement": 0.20,
    "format": 0.10,
    "consistency": 0.10,
    "provider_native": 0.05,
    "review_history": 0.10,
}
CONFLICT_PENALTY_WEIGHT = 0.25


@dataclass
class FieldConfidenceInput:
    provider_reliability_prior: float = 0.8
    field_quality_prior: float = 0.7
    freshness_factor: float | None = None
    agreement: float | None = None  # None when only one observation exists
    format_valid: bool | None = None
    consistency: float | None = None  # None when no cross-field check applies
    provider_native_confidence: float | None = None
    review_history: float | None = None  # 1 accepted / 0 rejected / None no history
    conflict_severity: float = 0.0
    extra: dict = dc_field(default_factory=dict)


@dataclass
class ConfidenceResult:
    score: float
    components: dict
    formula_version: str = FORMULA_VERSION


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def score_field(inp: FieldConfidenceInput) -> ConfidenceResult:
    components: dict[str, float | None] = {
        "prior": 0.5 * inp.provider_reliability_prior + 0.5 * inp.field_quality_prior,
        "freshness": inp.freshness_factor,
        "agreement": inp.agreement,
        "format": None if inp.format_valid is None else (1.0 if inp.format_valid else 0.0),
        "consistency": inp.consistency,
        "provider_native": inp.provider_native_confidence,
        "review_history": inp.review_history,
    }
    num = 0.0
    denom = 0.0
    for name, value in components.items():
        if value is None:
            continue
        w = WEIGHTS[name]
        num += w * _clamp(value)
        denom += w
    base = num / denom if denom else 0.0
    penalty = CONFLICT_PENALTY_WEIGHT * _clamp(inp.conflict_severity)
    score = _clamp(base - penalty)
    return ConfidenceResult(
        score=round(score, 4),
        components={
            **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in components.items()},
            "conflict_severity": inp.conflict_severity,
            "penalty": round(penalty, 4),
            "weighted_base": round(base, 4),
            **inp.extra,
        },
    )


def score_entity(
    field_scores: dict[str, float],
    *,
    required_fields: list[str] | None = None,
    requested_fields: list[str] | None = None,
    identity_match_certainty: float = 1.0,
) -> ConfidenceResult:
    required = set(required_fields or [])
    requested = set(requested_fields or field_scores.keys()) or set(field_scores.keys())
    if not field_scores:
        return ConfidenceResult(0.0, {"reason": "no scored fields"})
    num = denom = 0.0
    for f, s in field_scores.items():
        w = 2.0 if f in required else 1.0
        num += w * s
        denom += w
    mean = num / denom
    # Missingness: penalize requested-but-unscored fields (missing values).
    fill_ratio = len([f for f in requested if f in field_scores]) / max(1, len(requested))
    fill_adjustment = 0.6 + 0.4 * fill_ratio  # missing everything still leaves prior-driven floor
    score = _clamp(mean * fill_adjustment * _clamp(identity_match_certainty))
    return ConfidenceResult(
        round(score, 4),
        {
            "field_mean": round(mean, 4),
            "fill_ratio": round(fill_ratio, 4),
            "fill_adjustment": round(fill_adjustment, 4),
            "identity_match_certainty": identity_match_certainty,
            "n_fields": len(field_scores),
        },
    )


def score_sync(
    entity_score: float,
    synced_field_scores: dict[str, float],
    *,
    unresolved_conflicts: int = 0,
) -> ConfidenceResult:
    floor = min([entity_score, *synced_field_scores.values()]) if synced_field_scores else entity_score
    penalty = min(0.3, 0.15 * unresolved_conflicts)
    score = _clamp(floor - penalty)
    return ConfidenceResult(
        round(score, 4),
        {
            "entity_score": entity_score,
            "min_field_score": round(floor, 4),
            "unresolved_conflicts": unresolved_conflicts,
            "penalty": round(penalty, 4),
        },
    )
