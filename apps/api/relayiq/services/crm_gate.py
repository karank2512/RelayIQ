"""CRM synchronization gate (ADR-008): every field must earn its way into the CRM.

Outcomes: write | no_write | secondary_property | require_approval | preserve_crm | mark_refresh.
Each field decision carries reasons; the whole gate summary is stored on the sync attempt.
Never silently overwrite a fresh CRM value with lower-confidence enrichment.
"""

from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, datetime

from relayiq.canonical.normalize import values_equivalent
from relayiq.enums import GateOutcome, ReviewTaskStatus, StalenessState
from relayiq.observability.metrics import CRM_GATE
from relayiq.services import policy as policy_service
from relayiq.services import staleness as staleness_service


@dataclass
class FieldGateInput:
    field_name: str
    new_value: str | None
    confidence: float | None
    has_unresolved_conflict: bool
    reconciliation_outcome: str | None
    staleness_state: StalenessState
    reviewer_decision: str | None = None  # accepted | overridden | rejected | None
    manually_locked: bool = False
    crm_value: str | None = None
    crm_value_updated_at: datetime | None = None


@dataclass
class FieldGateDecision:
    field_name: str
    outcome: GateOutcome
    reasons: list[str] = dc_field(default_factory=list)


def gate_field(
    session,
    tenant_id: str,
    entity_type: str,
    inp: FieldGateInput,
    *,
    min_confidence: float = 0.6,
    crm_write_enabled: bool = True,
) -> FieldGateDecision:
    reasons: list[str] = []
    f = inp.field_name

    def out(o: GateOutcome, why: str) -> FieldGateDecision:
        reasons.append(why)
        CRM_GATE.labels(outcome=o.value).inc()
        return FieldGateDecision(f, o, reasons)

    if not crm_write_enabled:
        return out(GateOutcome.NO_WRITE, "campaign policy disables CRM writes")
    if not policy_service.crm_write_allowed(session, tenant_id, entity_type, f):
        return out(GateOutcome.NO_WRITE, "tenant policy blocks CRM writes for this field")
    if inp.new_value is None:
        return out(GateOutcome.NO_WRITE, "no canonical value to write")
    if inp.manually_locked:
        return out(GateOutcome.PRESERVE_CRM, "field is manually locked by an operator")
    if inp.reviewer_decision == ReviewTaskStatus.REJECTED.value:
        return out(GateOutcome.NO_WRITE, "reviewer rejected this value")

    # Unresolved conflicts never reach the CRM.
    if inp.has_unresolved_conflict and inp.reviewer_decision is None:
        return out(GateOutcome.REQUIRE_APPROVAL, "unresolved provider conflict — needs review sign-off")

    # Reviewer acceptance overrides the confidence threshold (human judgment wins).
    reviewer_approved = inp.reviewer_decision in ("accepted", "overridden", "corrected")
    if not reviewer_approved and (inp.confidence is None or inp.confidence < min_confidence):
        conf = "unknown" if inp.confidence is None else f"{inp.confidence:.2f}"
        return out(
            GateOutcome.REQUIRE_APPROVAL,
            f"confidence {conf} below sync threshold {min_confidence:.2f}",
        )

    # Staleness: don't push data we ourselves consider expired.
    if inp.staleness_state == StalenessState.EXPIRED:
        return out(GateOutcome.MARK_REFRESH, "canonical value is expired — scheduled for refresh instead")
    if inp.staleness_state == StalenessState.STALE and not reviewer_approved:
        return out(GateOutcome.MARK_REFRESH, "canonical value is stale — refresh before syncing")

    # Existing CRM value comparison.
    if inp.crm_value not in (None, ""):
        if values_equivalent(f, str(inp.crm_value), str(inp.new_value)):
            return out(GateOutcome.NO_WRITE, "CRM already holds an equivalent value")
        crm_age = staleness_service.age_days_from(inp.crm_value_updated_at)
        thresholds = staleness_service.get_thresholds(session, tenant_id, entity_type, f)
        crm_state = staleness_service.classify_age(crm_age, thresholds)
        if crm_state == StalenessState.FRESH and not reviewer_approved:
            if (inp.confidence or 0) >= 0.85:
                reasons.append(
                    f"CRM value '{inp.crm_value}' is fresh but enrichment confidence "
                    f"{inp.confidence:.2f} is high — writing to secondary property for operator comparison"
                )
                CRM_GATE.labels(outcome=GateOutcome.SECONDARY_PROPERTY.value).inc()
                return FieldGateDecision(f, GateOutcome.SECONDARY_PROPERTY, reasons)
            return out(
                GateOutcome.PRESERVE_CRM,
                f"CRM value is fresh ({crm_age:.0f}d old) and enrichment confidence does not "
                "clearly beat it — preserving CRM value",
            )
        reasons.append(
            f"overwriting {crm_state.value} CRM value '{inp.crm_value}' "
            f"(confidence {inp.confidence if inp.confidence is not None else 'n/a'})"
        )

    if reviewer_approved:
        reasons.append(f"reviewer decision '{inp.reviewer_decision}' authorizes the write")
    reasons.append("passed confidence, conflict, staleness, and CRM-comparison checks")
    CRM_GATE.labels(outcome=GateOutcome.WRITE.value).inc()
    return FieldGateDecision(f, GateOutcome.WRITE, reasons)


def gate_summary(decisions: list[FieldGateDecision]) -> dict:
    return {
        "writable": [d.field_name for d in decisions if d.outcome == GateOutcome.WRITE],
        "secondary": [d.field_name for d in decisions if d.outcome == GateOutcome.SECONDARY_PROPERTY],
        "blocked": {
            d.field_name: d.outcome.value
            for d in decisions
            if d.outcome not in (GateOutcome.WRITE, GateOutcome.SECONDARY_PROPERTY)
        },
        "decided_at": datetime.now(UTC).isoformat(),
    }
