"""Typed request/response models for the enrichment sidecar API (Clay-compatible contract)."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

CONTACT_FIELDS = [
    "first_name", "last_name", "full_name", "work_email", "job_title", "seniority",
    "department", "country", "linkedin_url",
]
ACCOUNT_FIELDS = [
    "name", "website", "root_domain", "linkedin_url", "industry", "sub_industry",
    "employee_count", "annual_revenue_usd", "hq_city", "hq_state", "hq_country",
    "company_type", "founded_year", "technology_signals",
]


class EntityPayload(BaseModel):
    """The inbound row (from Clay, a CRM, or manual entry). Identifier fields only —
    everything else is what enrichment is for."""

    external_crm_id: str | None = Field(default=None, max_length=128)
    # contact identifiers
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    full_name: str | None = Field(default=None, max_length=240)
    work_email: str | None = Field(default=None, max_length=320)
    country: str | None = Field(default=None, max_length=80)
    # account identifiers
    name: str | None = Field(default=None, max_length=240, description="Company name")
    company_name: str | None = Field(default=None, max_length=240)
    website: str | None = Field(default=None, max_length=500)
    root_domain: str | None = Field(default=None, max_length=253)
    company_domain: str | None = Field(default=None, max_length=253)
    employee_count: int | None = Field(default=None, ge=0, le=10_000_000)


class EnrichmentRequestIn(BaseModel):
    entity_type: Literal["contact", "account"]
    entity: EntityPayload
    requested_fields: list[str] = Field(min_length=1, max_length=30)
    campaign_id: str | None = None
    budget_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)
    callback_url: str | None = Field(default=None, max_length=1000)
    metadata: dict = Field(default_factory=dict)
    mode: Literal["sync", "async"] = "sync"
    dry_run: bool = False

    @field_validator("requested_fields")
    @classmethod
    def _known_fields(cls, v: list[str], info) -> list[str]:
        entity_type = info.data.get("entity_type")
        allowed = set(CONTACT_FIELDS if entity_type == "contact" else ACCOUNT_FIELDS)
        unknown = [f for f in v if f not in allowed]
        if unknown:
            raise ValueError(f"unknown fields for {entity_type}: {unknown}")
        return list(dict.fromkeys(v))  # dedupe, preserve order

    @field_validator("metadata")
    @classmethod
    def _metadata_size(cls, v: dict) -> dict:
        import json

        if len(json.dumps(v)) > 8192:
            raise ValueError("metadata too large (8KB max)")
        return v


class DecideOut(BaseModel):
    decision: str
    reasons: list[str]
    fields_to_enrich: list[str]
    fields_from_cache: dict
    estimated_cost_credits: float
    budget_warning: bool = False


class JobOut(BaseModel):
    id: str
    status: str
    entity_type: str
    entity_id: str | None
    pre_decision: str | None
    decision_reasons: list
    requested_fields: list
    estimated_cost_credits: float
    actual_cost_credits: float
    result_summary: dict
    error: str | None
    trace_id: str | None
    batch_id: str | None
    dry_run: bool
    created_at: str | None
    finished_at: str | None

    @classmethod
    def from_model(cls, j) -> "JobOut":
        return cls(
            id=j.id, status=j.status, entity_type=j.entity_type, entity_id=j.entity_id,
            pre_decision=j.pre_decision, decision_reasons=j.decision_reasons or [],
            requested_fields=j.requested_fields or [],
            estimated_cost_credits=float(j.estimated_cost_credits or 0),
            actual_cost_credits=float(j.actual_cost_credits or 0),
            result_summary=j.result_summary or {}, error=j.error, trace_id=j.trace_id,
            batch_id=j.batch_id, dry_run=j.dry_run,
            created_at=j.created_at.isoformat() if j.created_at else None,
            finished_at=j.finished_at.isoformat() if j.finished_at else None,
        )


class BatchRequestIn(BaseModel):
    entity_type: Literal["contact", "account"]
    rows: list[EntityPayload] = Field(min_length=1, max_length=500)
    requested_fields: list[str] = Field(min_length=1, max_length=30)
    campaign_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)
    callback_url: str | None = Field(default=None, max_length=1000)
    dry_run: bool = False


class BatchOut(BaseModel):
    batch_id: str
    job_ids: list[str]
    queued: int


class WebhookEnrichmentPayload(BaseModel):
    """Body of POST /v1/webhooks/enrichment (HMAC-signed)."""

    event: Literal["row.created", "row.updated", "enrichment.requested"]
    tenant_slug: str = Field(max_length=80)
    entity_type: Literal["contact", "account"]
    entity: EntityPayload
    requested_fields: list[str] = Field(min_length=1, max_length=30)
    campaign_id: str | None = None
    metadata: dict = Field(default_factory=dict)
