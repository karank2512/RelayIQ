"""Tenants, users, audit events, policy decisions."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from relayiq.enums import Role
from relayiq.models.base import Base, JSONVariant, PKMixin, TenantMixin, TimestampMixin


class Tenant(Base, PKMixin, TimestampMixin):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)


class User(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_tenant_email", "tenant_id", "email", unique=True),)

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=Role.ANALYST.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AuditEvent(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_tenant_object", "tenant_id", "object_type", "object_id"),
        Index("ix_audit_tenant_created", "tenant_id", "created_at"),
    )

    actor_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False, default="user")  # user|system|webhook
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    object_type: Mapped[str] = mapped_column(String(60), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PolicyDecision(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "policy_decisions"
    __table_args__ = (Index("ix_policy_tenant_entity", "tenant_id", "entity_type", "entity_id"),)

    policy_key: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)  # allow|deny
    reasons: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    context: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)


class Suppression(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "suppressions"
    __table_args__ = (Index("ix_suppressions_tenant_kind_value", "tenant_id", "kind", "value", unique=True),)

    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # domain|email|company_name
    value: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
