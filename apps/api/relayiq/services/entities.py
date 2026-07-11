"""Canonical entity resolution: match-or-create accounts/contacts and apply reconciled
canonical field values back onto entity columns."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.canonical.normalize import (
    extract_root_domain,
    normalize_company_name,
    normalize_email,
    normalize_job_title,
)
from relayiq.enums import EntityType
from relayiq.models import Account, CanonicalFieldValue, Contact

# Canonical field name -> entity column (fields not listed only live in canonical_field_values)
ACCOUNT_COLUMNS = {
    "name": "name", "website": "website", "root_domain": "root_domain",
    "linkedin_url": "linkedin_url", "industry": "industry", "sub_industry": "sub_industry",
    "employee_count": "employee_count", "employee_range": "employee_range",
    "annual_revenue_usd": "annual_revenue_usd", "hq_city": "hq_city", "hq_state": "hq_state",
    "hq_country": "hq_country", "company_type": "company_type", "founded_year": "founded_year",
}
CONTACT_COLUMNS = {
    "first_name": "first_name", "last_name": "last_name", "full_name": "full_name",
    "work_email": "work_email", "job_title": "job_title", "seniority": "seniority",
    "department": "department", "country": "country", "linkedin_url": "linkedin_url",
}
INT_FIELDS = {"employee_count", "annual_revenue_usd", "founded_year"}


def match_or_create_account(session: Session, tenant_id: str, data: dict) -> tuple[Account, bool, float]:
    """Returns (account, created, identity_match_certainty)."""
    domain = extract_root_domain(data.get("root_domain") or data.get("website"))
    norm_name = normalize_company_name(data.get("name") or data.get("company_name"))
    account = None
    certainty = 1.0
    if domain:
        account = session.execute(
            select(Account).where(Account.tenant_id == tenant_id, Account.root_domain == domain)
        ).scalars().first()
    if account is None and norm_name:
        account = session.execute(
            select(Account).where(Account.tenant_id == tenant_id, Account.normalized_name == norm_name)
        ).scalars().first()
        if account is not None:
            certainty = 0.8 if domain else 0.9  # name-only match is weaker; conflicting domain weaker still
            if domain and account.root_domain and account.root_domain != domain:
                certainty = 0.5
    if account is not None:
        return account, False, certainty

    account = Account(
        tenant_id=tenant_id,
        external_crm_id=data.get("external_crm_id"),
        name=data.get("name") or data.get("company_name"),
        normalized_name=norm_name,
        website=data.get("website"),
        root_domain=domain,
    )
    session.add(account)
    session.flush()
    return account, True, 1.0


def match_or_create_contact(session: Session, tenant_id: str, data: dict) -> tuple[Contact, bool, float]:
    email = normalize_email(data.get("work_email"))
    contact = None
    certainty = 1.0
    if email:
        contact = session.execute(
            select(Contact).where(Contact.tenant_id == tenant_id, Contact.work_email == email)
        ).scalars().first()
    if contact is None and data.get("external_crm_id"):
        contact = session.execute(
            select(Contact).where(
                Contact.tenant_id == tenant_id, Contact.external_crm_id == data["external_crm_id"]
            )
        ).scalars().first()
        if contact is not None:
            certainty = 0.9
    if contact is not None:
        return contact, False, certainty

    full_name = data.get("full_name") or " ".join(
        p for p in [data.get("first_name"), data.get("last_name")] if p
    ) or None
    contact = Contact(
        tenant_id=tenant_id,
        external_crm_id=data.get("external_crm_id"),
        first_name=data.get("first_name"),
        last_name=data.get("last_name"),
        full_name=full_name,
        work_email=email,
        company_name=data.get("company_name"),
        company_domain=extract_root_domain(data.get("company_domain") or data.get("website")),
        country=data.get("country"),
    )
    session.add(contact)
    session.flush()
    return contact, True, 1.0


def entity_lookup_key(entity_type: str, entity) -> str:
    """Key providers/cache use to address this entity (email for contacts, domain for accounts)."""
    if entity_type == EntityType.CONTACT.value:
        return (entity.work_email or "").lower()
    return (entity.root_domain or "").lower()


def upsert_canonical_value(
    session: Session,
    tenant_id: str,
    entity_type: str,
    entity_id: str,
    field_name: str,
    *,
    value: str | None,
    normalized_value: str | None,
    confidence: float | None,
    observation_id: str | None,
    reconciliation_decision_id: str | None,
    staleness_state: str,
    source_kind: str = "provider",
    verified_at: datetime | None = None,
) -> CanonicalFieldValue:
    row = session.execute(
        select(CanonicalFieldValue).where(
            CanonicalFieldValue.tenant_id == tenant_id,
            CanonicalFieldValue.entity_type == entity_type,
            CanonicalFieldValue.entity_id == entity_id,
            CanonicalFieldValue.field_name == field_name,
        )
    ).scalar_one_or_none()
    if row is None:
        row = CanonicalFieldValue(
            tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id, field_name=field_name
        )
        session.add(row)
    if row.locked:
        return row  # manually locked values are never auto-overwritten
    row.value = str(value) if value is not None else None
    row.normalized_value = str(normalized_value) if normalized_value is not None else None
    row.confidence = confidence
    row.selected_observation_id = observation_id
    row.reconciliation_decision_id = reconciliation_decision_id
    row.staleness_state = staleness_state
    row.source_kind = source_kind
    row.last_verified_at = verified_at or datetime.now(UTC)
    session.flush()
    return row


def apply_canonical_to_entity(session: Session, entity_type: str, entity, field_name: str,
                              value: str | None) -> None:
    """Mirror an accepted canonical value onto the entity column (for list views/CRM mapping)."""
    columns = CONTACT_COLUMNS if entity_type == EntityType.CONTACT.value else ACCOUNT_COLUMNS
    col = columns.get(field_name)
    if col is None:
        return
    v: object | None = value
    if field_name in INT_FIELDS and value is not None:
        try:
            v = int(float(str(value)))
        except (TypeError, ValueError):
            return
    setattr(entity, col, v)
    if field_name == "job_title" and value:
        entity.normalized_job_title = normalize_job_title(str(value))
    if field_name == "employee_count" and isinstance(v, int):
        from relayiq.canonical.normalize import employee_count_to_range

        entity.employee_range = employee_count_to_range(v)
    entity.last_verified_at = datetime.now(UTC)
    session.flush()
