"""Canonical entities: accounts, contacts, external identifiers, canonical field values.

Canonical field values are selected from field observations by the reconciliation
engine — observations are never overwritten (ADR-006).
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from relayiq.enums import EmailStatus, RecordStatus
from relayiq.models.base import Base, JSONVariant, PKMixin, TenantMixin, TimestampMixin


class Account(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "accounts"
    __table_args__ = (
        Index("ix_accounts_tenant_domain", "tenant_id", "root_domain"),
        Index("ix_accounts_tenant_normname", "tenant_id", "normalized_name"),
    )

    external_crm_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    normalized_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    root_domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sub_industry: Mapped[str | None] = mapped_column(String(120), nullable=True)
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    employee_range: Mapped[str | None] = mapped_column(String(20), nullable=True)  # e.g. "51-200"
    annual_revenue_usd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hq_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    hq_state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    hq_country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    company_type: Mapped[str | None] = mapped_column(String(60), nullable=True)  # private|public|nonprofit...
    founded_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    technology_signals: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RecordStatus.ACTIVE.value)
    record_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)


class Contact(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_tenant_email", "tenant_id", "work_email"),
        Index("ix_contacts_tenant_account", "tenant_id", "account_id"),
    )

    external_crm_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    first_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(250), nullable=True)
    work_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_status: Mapped[str] = mapped_column(String(20), nullable=False, default=EmailStatus.UNKNOWN.value)
    job_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    normalized_job_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    seniority: Mapped[str | None] = mapped_column(String(40), nullable=True)
    department: Mapped[str | None] = mapped_column(String(80), nullable=True)
    account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    company_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    company_domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RecordStatus.ACTIVE.value)
    record_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)


class ExternalIdentifier(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "external_identifiers"
    __table_args__ = (
        Index("ix_extid_unique", "tenant_id", "system", "external_id", unique=True),
        Index("ix_extid_entity", "tenant_id", "entity_type", "entity_id"),
    )

    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    system: Mapped[str] = mapped_column(String(40), nullable=False)  # hubspot|salesforce|clay|simulator
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)


class CanonicalFieldValue(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "canonical_field_values"
    __table_args__ = (
        Index("ix_cfv_unique", "tenant_id", "entity_type", "entity_id", "field_name", unique=True),
    )

    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    field_name: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    selected_observation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reconciliation_decision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    staleness_state: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="provider")  # provider|crm|manual
    locked: Mapped[bool] = mapped_column(default=False, nullable=False)  # manual lock blocks CRM overwrite
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
