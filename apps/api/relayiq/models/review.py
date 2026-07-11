"""Manual review queue: tasks + decisions. Decisions preserve prior state (reversible, audited)."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from relayiq.enums import ReviewTaskStatus
from relayiq.models.base import Base, JSONVariant, PKMixin, TenantMixin, TimestampMixin


class ReviewTask(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "review_tasks"
    __table_args__ = (
        Index("ix_review_tenant_status", "tenant_id", "status"),
        Index("ix_review_entity", "tenant_id", "entity_type", "entity_id"),
    )

    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(80), nullable=True)  # NULL = record-level
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    reconciliation_decision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ReviewTaskStatus.PENDING.value)
    priority: Mapped[int] = mapped_column(nullable=False, default=50)  # 0 highest
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_value: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    suggested_observation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(36), nullable=True)
    first_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewDecision(Base, PKMixin, TenantMixin, TimestampMixin):
    """Append-only. `previous_state` snapshots the task + canonical value before this action,
    which is what makes reversals lossless."""

    __tablename__ = "review_decisions"
    __table_args__ = (Index("ix_review_decisions_task", "task_id"),)

    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)  # ReviewAction
    selected_observation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    corrected_value: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_state: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    reverses_decision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
