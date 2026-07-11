"""Staleness engine: classify field age against configurable per-field policies.

Precedence: tenant policy row > global policy row (tenant_id NULL) > builtin default.
Staleness feeds cache reuse, routing, confidence decay, CRM gating, and review priority.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.enums import StalenessState
from relayiq.models import StalenessPolicy


@dataclass(frozen=True)
class Thresholds:
    fresh_days: int
    aging_days: int
    stale_days: int  # beyond => expired


# Builtin defaults (days). Aligned with the spec examples.
DEFAULTS: dict[tuple[str, str], Thresholds] = {
    ("contact", "job_title"): Thresholds(30, 60, 90),
    ("contact", "seniority"): Thresholds(30, 60, 90),
    ("contact", "work_email"): Thresholds(30, 60, 90),
    ("contact", "email_status"): Thresholds(30, 60, 90),
    ("account", "employee_count"): Thresholds(90, 135, 270),
    ("account", "employee_range"): Thresholds(90, 135, 270),
    ("account", "industry"): Thresholds(180, 270, 540),
    ("account", "root_domain"): Thresholds(365, 550, 730),
    ("account", "website"): Thresholds(365, 550, 730),
}
FALLBACK = Thresholds(90, 180, 365)


def get_thresholds(session: Session | None, tenant_id: str | None, entity_type: str, field_name: str) -> Thresholds:
    if session is not None:
        rows = session.execute(
            select(StalenessPolicy).where(
                StalenessPolicy.entity_type == entity_type,
                StalenessPolicy.field_name == field_name,
                (StalenessPolicy.tenant_id == tenant_id) | (StalenessPolicy.tenant_id.is_(None)),
            )
        ).scalars().all()
        tenant_row = next((r for r in rows if r.tenant_id == tenant_id and tenant_id), None)
        global_row = next((r for r in rows if r.tenant_id is None), None)
        row = tenant_row or global_row
        if row:
            return Thresholds(row.fresh_days, row.aging_days, row.stale_days)
    return DEFAULTS.get((entity_type, field_name), FALLBACK)


def classify_age(age_days: float | None, t: Thresholds) -> StalenessState:
    if age_days is None:
        return StalenessState.UNKNOWN
    if age_days <= t.fresh_days:
        return StalenessState.FRESH
    if age_days <= t.aging_days:
        return StalenessState.AGING
    if age_days <= t.stale_days:
        return StalenessState.STALE
    return StalenessState.EXPIRED


def age_days_from(ts: datetime | None, now: datetime | None = None) -> float | None:
    if ts is None:
        return None
    now = now or datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return max(0.0, (now - ts).total_seconds() / 86400)


def classify(
    session: Session | None,
    tenant_id: str | None,
    entity_type: str,
    field_name: str,
    *,
    age_days: float | None = None,
    verified_at: datetime | None = None,
    now: datetime | None = None,
) -> StalenessState:
    if age_days is None:
        age_days = age_days_from(verified_at, now)
    return classify_age(age_days, get_thresholds(session, tenant_id, entity_type, field_name))


def freshness_factor(age_days: float | None, t: Thresholds) -> float:
    """Exponential decay in [0,1] used by the confidence model: 1.0 at age 0,
    ~0.5 at stale_days, → 0 beyond. factor = exp(-ln(2) * age/stale_days)."""
    if age_days is None:
        return 0.5  # unknown age: neutral prior
    return math.exp(-math.log(2) * age_days / max(1, t.stale_days))


def is_reusable(state: StalenessState) -> bool:
    """Fresh/aging values can be served without re-enrichment; stale triggers refresh;
    expired/unknown are treated as missing."""
    return state in (StalenessState.FRESH, StalenessState.AGING)


def review_priority_boost(state: StalenessState) -> int:
    return {StalenessState.EXPIRED: -20, StalenessState.STALE: -10}.get(state, 0)
