"""Campaigns, budgets, enrichment jobs, workflow steps, routing/reconciliation/confidence
decisions, idempotency records, cost ledger, staleness & routing policies."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from relayiq.enums import BudgetKind, BudgetPeriod, CampaignStatus, IdempotencyStatus, JobStatus
from relayiq.models.base import Base, JSONVariant, PKMixin, TenantMixin, TimestampMixin


class Campaign(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "campaigns"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CampaignStatus.ACTIVE.value)
    # Declarative filters evaluated by the pre-enrichment engine,
    # e.g. {"required_identifiers": ["work_email"], "allowed_countries": [...], "min_employee_count": 10}
    filters: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    required_fields: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    min_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.6)
    routing_policy_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    crm_write_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Budget(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "budgets"
    __table_args__ = (Index("ix_budgets_tenant_campaign", "tenant_id", "campaign_id"),)

    campaign_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default=BudgetKind.HARD.value)
    period: Mapped[str] = mapped_column(String(10), nullable=False, default=BudgetPeriod.LIFETIME.value)
    limit_credits: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    spent_credits: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    reserved_credits: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    warning_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)  # fraction
    per_record_max: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    per_field_max: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    provider_max: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Degradation mode once warning threshold is crossed: cache_only|cheapest|required_fields_only|stop
    degradation_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="cheapest")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class EnrichmentJob(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "enrichment_jobs"
    __table_args__ = (
        Index("ix_jobs_tenant_status", "tenant_id", "status"),
        Index("ix_jobs_tenant_entity", "tenant_id", "entity_type", "entity_id"),
        Index("ix_jobs_tenant_created", "tenant_id", "created_at"),
    )

    campaign_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    budget_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    requested_fields: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=JobStatus.RECEIVED.value)
    pre_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)  # PreDecision
    decision_reasons: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    callback_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    metadata_passthrough: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    estimated_cost_credits: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    actual_cost_credits: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    result_summary: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowStep(Base, PKMixin, TimestampMixin):
    __tablename__ = "workflow_steps"
    __table_args__ = (Index("ix_steps_job", "job_id", "sequence"),)

    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("enrichment_jobs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    detail: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RoutingDecision(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "routing_decisions"
    __table_args__ = (Index("ix_routing_job_field", "job_id", "field_name"),)

    job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    field_name: Mapped[str] = mapped_column(String(80), nullable=False)
    candidates: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    selected_provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    rejected_providers: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    factors: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    strategy: Mapped[str] = mapped_column(String(40), nullable=False, default="static")
    expected_cost_credits: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    actual_cost_credits: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fallback_detail: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)


class ReconciliationDecision(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "reconciliation_decisions"
    __table_args__ = (Index("ix_recon_entity_field", "tenant_id", "entity_type", "entity_id", "field_name"),)

    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    field_name: Mapped[str] = mapped_column(String(80), nullable=False)
    observation_ids: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)  # ReconciliationOutcome
    chosen_observation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    chosen_value: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    factors: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    conflict_severity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class ConfidenceEvaluation(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "confidence_evaluations"
    __table_args__ = (Index("ix_conf_entity", "tenant_id", "entity_type", "entity_id", "level"),)

    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False)  # field|entity|sync
    score: Mapped[float] = mapped_column(Float, nullable=False)
    components: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    formula_version: Mapped[str] = mapped_column(String(20), nullable=False, default="rules-v1")


class IdempotencyRecord(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "idempotency_records"
    __table_args__ = (Index("ix_idem_unique", "tenant_id", "scope", "key", unique=True),)

    scope: Mapped[str] = mapped_column(String(40), nullable=False)  # enrichment|webhook|crm_sync|review
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=IdempotencyStatus.IN_PROGRESS.value)
    response_snapshot: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CostLedgerEntry(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "cost_ledger_entries"
    __table_args__ = (
        Index("ix_ledger_tenant_created", "tenant_id", "created_at"),
        Index("ix_ledger_campaign", "tenant_id", "campaign_id"),
        Index("ix_ledger_provider", "tenant_id", "provider_key"),
        Index("ix_ledger_job", "job_id"),
    )

    campaign_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)  # enrich_field|enrich_call|crm_sync
    fields_requested: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    estimated_cost_credits: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    actual_cost_credits: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    cost_unit: Mapped[str] = mapped_column(String(12), nullable=False, default="credit")
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)  # ProviderOutcome
    cache_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # CacheStatus
    was_redundant: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    avoided_cost_credits: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    result_accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # set after reconciliation
    record_rejected_later: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    spent_on_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class StalenessPolicy(Base, PKMixin, TimestampMixin):
    """tenant_id NULL = global default; tenant rows override."""

    __tablename__ = "staleness_policies"
    __table_args__ = (Index("ix_staleness_unique", "tenant_id", "entity_type", "field_name", unique=True),)

    tenant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    field_name: Mapped[str] = mapped_column(String(80), nullable=False)
    fresh_days: Mapped[int] = mapped_column(Integer, nullable=False)
    aging_days: Mapped[int] = mapped_column(Integer, nullable=False)
    stale_days: Mapped[int] = mapped_column(Integer, nullable=False)  # beyond stale_days => expired


class RoutingPolicy(Base, PKMixin, TenantMixin, TimestampMixin):
    __tablename__ = "routing_policies"
    __table_args__ = (Index("ix_routing_policy_tenant_name", "tenant_id", "name", unique=True),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Parsed policy document (see docs/architecture/routing-policy.md); YAML accepted at the API edge.
    document: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
