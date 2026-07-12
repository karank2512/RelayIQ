"""Webhook delivery records — dedup + audit trail for inbound webhooks."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from relayiq.models.base import Base, JSONVariant, PKMixin, TenantMixin, TimestampMixin


class WebhookDelivery(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_delivery_unique", "tenant_id", "source", "delivery_id", unique=True),)

    source: Mapped[str] = mapped_column(String(40), nullable=False)  # clay|crm|test
    delivery_id: Mapped[str] = mapped_column(String(128), nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timestamp_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received")  # received|processed|rejected  # noqa: E501
    reject_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # Minimal event metadata only — full payloads are not retained (ADR-012)
    event_meta: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
