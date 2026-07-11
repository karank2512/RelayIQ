"""Field observations — one row per provider-returned field value. Never overwritten (ADR-006)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from relayiq.models.base import Base, JSONVariant, PKMixin, TenantMixin, TimestampMixin


class FieldObservation(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "field_observations"
    __table_args__ = (
        Index("ix_obs_entity_field", "tenant_id", "entity_type", "entity_id", "field_name"),
        Index("ix_obs_provider", "tenant_id", "provider_key", "field_name"),
        Index("ix_obs_request", "provider_request_id"),
    )

    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    field_name: Mapped[str] = mapped_column(String(80), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    provider_key: Mapped[str] = mapped_column(String(60), nullable=False)  # alpha|beta|crm|manual
    provider_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cost_credits: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    cost_unit: Mapped[str] = mapped_column(String(12), nullable=False, default="credit")
    provider_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    internal_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    staleness_state: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    validation_results: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_rejected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workflow_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # enrichment job id
