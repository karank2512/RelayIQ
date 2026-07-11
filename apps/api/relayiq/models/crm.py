"""CRM connections, sync attempts, and the built-in CRM simulator store."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from relayiq.enums import CrmSystem, SyncStatus
from relayiq.models.base import Base, JSONVariant, PKMixin, TenantMixin, TimestampMixin


class CrmConnection(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "crm_connections"

    system: Mapped[str] = mapped_column(String(20), nullable=False, default=CrmSystem.SIMULATOR.value)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="simulator")  # simulator|live|dry_run
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Non-secret config only (property mappings, rate limits). Credentials live in env/secret store.
    config: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)


class CrmSyncAttempt(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "crm_sync_attempts"
    __table_args__ = (
        Index("ix_sync_entity", "tenant_id", "entity_type", "entity_id"),
        Index("ix_sync_status_created", "tenant_id", "status", "created_at"),
        Index("ix_sync_idem", "tenant_id", "idempotency_key", unique=True),
    )

    connection_id: Mapped[str] = mapped_column(String(36), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Per-field: {field: {"before": ..., "after": ..., "gate": "write|no_write|...", "reasons": [...]}}
    field_changes: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    gate_summary: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=SyncStatus.PENDING.value)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CrmSimRecord(Base, PKMixin, TenantMixin, TimestampMixin):
    """The CRM simulator's own store — lets reviewers see what 'the CRM' contains."""

    __tablename__ = "crm_sim_records"
    __table_args__ = (Index("ix_crmsim_unique", "tenant_id", "object_type", "external_id", unique=True),)

    object_type: Mapped[str] = mapped_column(String(20), nullable=False)  # company|contact
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    properties: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    # Per-property freshness so the sync gate can compare CRM value age
    property_updated_at: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
