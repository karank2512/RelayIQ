"""Permitted-use policy engine — a *technical control*, not legal compliance (see docs/security).

Checks suppression lists, tenant/campaign field restrictions, and CRM-write restrictions.
Every evaluation is persisted as a PolicyDecision row for auditability.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.models import PolicyDecision, Suppression, Tenant

# Fields a tenant may forbid via tenant.settings["restricted_fields"] (e.g. linkedin_url
# when contractually not permitted).
DEFAULT_RESTRICTED_FIELDS: set[str] = set()


@dataclass
class PolicyResult:
    allowed: bool
    reasons: list[str]
    blocked_fields: list[str]


def _norm(v: str | None) -> str:
    return (v or "").strip().lower()


def check_suppression(session: Session, tenant_id: str, *, domain: str | None, email: str | None,
                      company_name: str | None) -> list[str]:
    reasons = []
    now = datetime.now(UTC)
    rows = session.execute(select(Suppression).where(Suppression.tenant_id == tenant_id)).scalars().all()
    for s in rows:
        exp = s.expires_at
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if exp is not None and exp < now:
            continue
        val = _norm(s.value)
        if s.kind == "domain" and val and val == _norm(domain):
            reasons.append(f"domain '{val}' is suppressed ({s.reason or 'no reason recorded'})")
        elif s.kind == "email" and val and val == _norm(email):
            reasons.append(f"email is suppressed ({s.reason or 'no reason recorded'})")
        elif s.kind == "company_name" and val and val == _norm(company_name):
            reasons.append(f"company '{val}' is suppressed ({s.reason or 'no reason recorded'})")
    return reasons


def evaluate(
    session: Session,
    tenant_id: str,
    *,
    entity_type: str,
    entity_id: str | None,
    requested_fields: list[str],
    domain: str | None = None,
    email: str | None = None,
    company_name: str | None = None,
    campaign_restrictions: dict | None = None,
    persist: bool = True,
) -> PolicyResult:
    reasons: list[str] = []
    blocked_fields: list[str] = []

    suppression_reasons = check_suppression(
        session, tenant_id, domain=domain, email=email, company_name=company_name
    )
    reasons.extend(suppression_reasons)

    tenant = session.get(Tenant, tenant_id)
    restricted = set((tenant.settings or {}).get("restricted_fields", [])) if tenant else set()
    restricted |= DEFAULT_RESTRICTED_FIELDS
    if campaign_restrictions:
        restricted |= set(campaign_restrictions.get("restricted_fields", []))
    for f in requested_fields:
        if f in restricted:
            blocked_fields.append(f)
    if blocked_fields:
        reasons.append(f"fields not permitted by policy: {sorted(blocked_fields)}")

    allowed = not suppression_reasons  # blocked fields alone don't kill the request; they're dropped

    if persist:
        session.add(
            PolicyDecision(
                tenant_id=tenant_id,
                policy_key="permitted_use.v1",
                entity_type=entity_type,
                entity_id=entity_id,
                decision="allow" if allowed else "deny",
                reasons=reasons,
                context={"requested_fields": requested_fields, "blocked_fields": blocked_fields},
            )
        )
    return PolicyResult(allowed=allowed, reasons=reasons, blocked_fields=blocked_fields)


def crm_write_allowed(session: Session, tenant_id: str, entity_type: str, field_name: str) -> bool:
    tenant = session.get(Tenant, tenant_id)
    blocked = set((tenant.settings or {}).get("crm_write_blocked_fields", [])) if tenant else set()
    return field_name not in blocked
