"""Provider configuration, capabilities, requests/responses, health aggregates."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from relayiq.models.base import Base, JSONVariant, PKMixin, TenantMixin, TimestampMixin


class ProviderConfig(Base, PKMixin, TimestampMixin):
    """Global provider registry. tenant_id NULL = available to all tenants."""

    __tablename__ = "provider_configs"
    __table_args__ = (Index("ix_provider_key", "key", unique=True),)

    key: Mapped[str] = mapped_column(String(60), nullable=False)  # e.g. "alpha"
    tenant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    adapter: Mapped[str] = mapped_column(String(120), nullable=False)  # dotted path or registry key
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=8000)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reliability_prior: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)
    config: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)  # simulator knobs etc.


class ProviderCapability(Base, PKMixin, TimestampMixin):
    __tablename__ = "provider_capabilities"
    __table_args__ = (
        Index("ix_cap_unique", "provider_id", "entity_type", "field_name", unique=True),
    )

    provider_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("provider_configs.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    field_name: Mapped[str] = mapped_column(String(80), nullable=False)
    cost_credits: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=1)
    quality_prior: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)  # field-level prior


class ProviderRequest(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "provider_requests"
    __table_args__ = (
        Index("ix_preq_entity", "tenant_id", "entity_type", "entity_id"),
        Index("ix_preq_provider_created", "provider_key", "created_at"),
    )

    provider_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider_key: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    fields_requested: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)  # ProviderOutcome
    error_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_credits: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workflow_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ProviderResponse(Base, PKMixin, TimestampMixin):
    """Raw payload retained short-term for debugging/lineage; see ADR-012 retention policy."""

    __tablename__ = "provider_responses"

    provider_request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("provider_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    raw_payload: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    normalized_payload: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProviderHealthWindow(Base, PKMixin, TimestampMixin):
    """Rolling aggregate per provider per window (worker-maintained)."""

    __tablename__ = "provider_health_windows"
    __table_args__ = (Index("ix_health_provider_window", "provider_key", "window_start", unique=True),)

    provider_key: Mapped[str] = mapped_column(String(60), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    temp_fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    perm_fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timeout_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_limited_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    p50_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    p95_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    p99_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
