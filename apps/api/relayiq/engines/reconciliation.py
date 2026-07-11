"""Field-level conflict reconciliation (ADR-006).

All observations are preserved; this engine groups them by value-equivalence, scores the
groups, and picks an outcome with human-readable reasoning. It never deletes anything —
selection state lives on the observations, the decision in reconciliation_decisions.
"""

from dataclasses import dataclass

from relayiq.canonical.normalize import validate_field, values_equivalent
from relayiq.enums import ReconciliationOutcome, StalenessState
from relayiq.models import FieldObservation
from relayiq.services.staleness import Thresholds, freshness_factor

# Per-field conflict severity when values genuinely disagree (0..1, higher = more dangerous
# to auto-accept). Domains are identity-defining; a wrong domain poisons everything downstream.
FIELD_SEVERITY = {
    "root_domain": 0.9,
    "company_domain": 0.9,
    "website": 0.8,
    "work_email": 0.85,
    "name": 0.5,
    "company_name": 0.5,
    "job_title": 0.55,
    "seniority": 0.45,
    "industry": 0.5,
    "employee_count": 0.5,
    "employee_range": 0.5,
}
DEFAULT_SEVERITY = 0.5

AUTO_ACCEPT_MARGIN = 0.5  # winner must lead runner-up by this relative margin
WARNING_MAX_SEVERITY = 0.55


@dataclass
class ObservationScore:
    observation: FieldObservation
    weight: float
    detail: dict


@dataclass
class ReconcileResult:
    outcome: ReconciliationOutcome
    chosen: FieldObservation | None
    reasoning: str
    factors: dict
    conflict_severity: float
    agreement: float | None  # share of providers agreeing with chosen value (for confidence)


def _obs_weight(
    obs: FieldObservation,
    provider_priors: dict[str, float],
    thresholds: Thresholds,
) -> ObservationScore:
    prior = provider_priors.get(obs.provider_key, 0.7)
    age = None
    if obs.retrieved_at and obs.source_timestamp:
        age = max(0.0, (obs.retrieved_at - obs.source_timestamp).total_seconds() / 86400)
    fresh = freshness_factor(age, thresholds)
    native = obs.provider_confidence if obs.provider_confidence is not None else 0.8
    valid = validate_field(obs.field_name, obs.normalized_value or obs.raw_value)["valid"]
    weight = prior * fresh * native * (1.0 if valid else 0.35)
    return ObservationScore(
        obs, round(weight, 4),
        {"provider": obs.provider_key, "prior": prior, "freshness": round(fresh, 3),
         "native_confidence": native, "format_valid": valid, "age_days": age},
    )


def _severity_for(field_name: str, groups: list[list[ObservationScore]]) -> float:
    if len(groups) <= 1:
        return 0.0
    base = FIELD_SEVERITY.get(field_name, DEFAULT_SEVERITY)
    if field_name in ("employee_count", "employee_range"):
        from relayiq.canonical.normalize import employee_count_to_range, ranges_adjacent

        reps = []
        for g in groups:
            v = g[0].observation.normalized_value or g[0].observation.raw_value
            if field_name == "employee_count":
                try:
                    v = employee_count_to_range(int(float(str(v))))
                except (TypeError, ValueError):
                    v = None
            reps.append(v)
        if len(reps) == 2 and all(reps) and ranges_adjacent(reps[0], reps[1]):
            return 0.3  # adjacent buckets: mild disagreement
    return base


def reconcile_field(
    entity_type: str,
    field_name: str,
    observations: list[FieldObservation],
    *,
    provider_priors: dict[str, float],
    thresholds: Thresholds,
    crm_value: str | None = None,
    crm_state: StalenessState = StalenessState.UNKNOWN,
) -> ReconcileResult:
    live = [o for o in observations if not o.is_rejected and (o.normalized_value or o.raw_value) is not None]
    if not live:
        return ReconcileResult(
            ReconciliationOutcome.UNRESOLVED, None,
            f"No usable observations for {field_name}.", {"observations": 0}, 0.0, None,
        )

    scored = [_obs_weight(o, provider_priors, thresholds) for o in live]

    # All-invalid formats → reject everything.
    if all(not s.detail["format_valid"] for s in scored):
        return ReconcileResult(
            ReconciliationOutcome.REJECT_ALL, None,
            f"All {len(scored)} observation(s) for {field_name} failed format validation.",
            {"groups": [], "scores": [s.detail for s in scored]}, 1.0, None,
        )

    # Group by value-equivalence (normalized agreement counts as agreement).
    groups: list[list[ObservationScore]] = []
    for s in scored:
        val = s.observation.normalized_value or s.observation.raw_value
        for g in groups:
            gval = g[0].observation.normalized_value or g[0].observation.raw_value
            if values_equivalent(field_name, str(val), str(gval)):
                g.append(s)
                break
        else:
            groups.append([s])
    groups.sort(key=lambda g: sum(s.weight for s in g), reverse=True)
    top = groups[0]
    top_weight = sum(s.weight for s in top)
    runner_weight = sum(s.weight for s in groups[1]) if len(groups) > 1 else 0.0
    margin = (top_weight - runner_weight) / top_weight if top_weight else 0.0
    severity = _severity_for(field_name, groups)
    chosen = max(top, key=lambda s: s.weight).observation
    chosen_val = chosen.normalized_value or chosen.raw_value

    providers_seen = {s.observation.provider_key for s in scored}
    providers_agreeing = {s.observation.provider_key for s in top}
    agreement = (
        (len(providers_agreeing) - 1) / (len(providers_seen) - 1) if len(providers_seen) > 1 else None
    )

    factors = {
        "groups": [
            {
                "value": g[0].observation.normalized_value or g[0].observation.raw_value,
                "weight": round(sum(s.weight for s in g), 4),
                "providers": [s.observation.provider_key for s in g],
            }
            for g in groups
        ],
        "margin": round(margin, 4),
        "severity": severity,
        "crm_value": crm_value,
        "crm_state": crm_state.value,
        "scores": [s.detail for s in scored],
    }

    def names(g):
        return ", ".join(sorted({s.observation.provider_key for s in g}))

    # CRM alignment: a fresh CRM value that agrees with the winner reinforces it; a fresh
    # CRM value that disagrees with every provider group is retained (don't fight the CRM
    # without evidence — ADR-008).
    crm_fresh = crm_state in (StalenessState.FRESH, StalenessState.AGING)
    if crm_value is not None and crm_fresh:
        crm_in_top = values_equivalent(field_name, crm_value, str(chosen_val))
        crm_anywhere = any(
            values_equivalent(field_name, crm_value, str(g[0].observation.normalized_value or g[0].observation.raw_value))
            for g in groups
        )
        if not crm_anywhere and severity >= 0.5:
            return ReconcileResult(
                ReconciliationOutcome.RETAIN_CRM, None,
                f"CRM already holds a {crm_state.value} value ('{crm_value}') for {field_name} that no "
                f"provider corroborates; providers disagree with each other (severity {severity:.2f}). "
                "Retaining the CRM value rather than overwriting it on weak evidence.",
                factors, severity, agreement,
            )
        if crm_in_top:
            factors["crm_reinforces_winner"] = True

    if len(groups) == 1:
        n = len(scored)
        reasoning = (
            f"All {n} observation(s) agree on '{chosen_val}' for {field_name} "
            f"({names(top)}); selected the highest-weight source ({chosen.provider_key})."
            if n > 1
            else f"Single observation for {field_name} from {chosen.provider_key}: '{chosen_val}'."
        )
        return ReconcileResult(ReconciliationOutcome.AUTO_ACCEPT, chosen, reasoning, factors, 0.0, agreement)

    if margin >= AUTO_ACCEPT_MARGIN and severity <= WARNING_MAX_SEVERITY:
        reasoning = (
            f"Providers disagree on {field_name}: chose '{chosen_val}' from {names(top)} "
            f"(weight {top_weight:.2f}) over {names(groups[1])} (weight {runner_weight:.2f}); "
            f"margin {margin:.0%} clears the {AUTO_ACCEPT_MARGIN:.0%} auto-accept bar and "
            f"severity {severity:.2f} is moderate. Accepted with warning."
        )
        return ReconcileResult(
            ReconciliationOutcome.ACCEPT_WITH_WARNING, chosen, reasoning, factors, severity, agreement
        )

    reasoning = (
        f"Conflicting values for {field_name}: "
        + "; ".join(
            f"'{g[0].observation.normalized_value or g[0].observation.raw_value}' ({names(g)}, weight "
            f"{sum(s.weight for s in g):.2f})"
            for g in groups[:3]
        )
        + f". Margin {margin:.0%} below auto-accept bar or severity {severity:.2f} too high — "
        f"routed to human review with '{chosen_val}' suggested."
    )
    return ReconcileResult(ReconciliationOutcome.REQUIRE_REVIEW, chosen, reasoning, factors, severity, agreement)
