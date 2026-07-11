"""Shared domain enums. Stored as VARCHAR (non-native) for portability; values are stable API contract."""

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    REVIEWER = "reviewer"
    ANALYST = "analyst"  # read-only


class EntityType(StrEnum):
    CONTACT = "contact"
    ACCOUNT = "account"


class PreDecision(StrEnum):
    """Pre-enrichment decision engine outcomes."""

    REJECT = "reject"
    SKIP = "skip"
    USE_CACHE = "use_cache"
    ENRICH = "enrich"
    REVIEW = "review"
    BUDGET_BLOCK = "budget_block"
    POLICY_BLOCK = "policy_block"


class JobStatus(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    COMPLETED_CACHED = "completed_cached"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    BLOCKED_BUDGET = "blocked_budget"
    BLOCKED_POLICY = "blocked_policy"
    PARTIAL = "partial"
    FAILED = "failed"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StalenessState(StrEnum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class ProviderOutcome(StrEnum):
    SUCCESS = "success"
    TEMP_FAIL = "temp_fail"
    PERM_FAIL = "perm_fail"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    CIRCUIT_OPEN = "circuit_open"


class CacheStatus(StrEnum):
    HIT = "hit"
    STALE_HIT = "stale_hit"
    NEGATIVE_HIT = "negative_hit"
    MISS = "miss"
    BYPASS = "bypass"


class ReconciliationOutcome(StrEnum):
    AUTO_ACCEPT = "auto_accept"
    ACCEPT_WITH_WARNING = "accept_with_warning"
    REQUIRE_REVIEW = "require_review"
    REJECT_ALL = "reject_all"
    RETAIN_CRM = "retain_crm"
    UNRESOLVED = "unresolved"


class ReviewTaskStatus(StrEnum):
    PENDING = "pending"
    IN_REVIEW = "in_review"
    ACCEPTED = "accepted"
    OVERRIDDEN = "overridden"  # reviewer picked a non-suggested provider value
    CORRECTED = "corrected"  # reviewer typed a manual value
    REJECTED = "rejected"
    DEFERRED = "deferred"
    REVERSED = "reversed"


class ReviewAction(StrEnum):
    CLAIM = "claim"
    ACCEPT_SUGGESTED = "accept_suggested"
    SELECT_ALTERNATIVE = "select_alternative"
    CORRECT_VALUE = "correct_value"
    REJECT = "reject"
    DEFER = "defer"
    ADD_NOTE = "add_note"
    REVERSE = "reverse"


class SyncGateOutcome(StrEnum):
    WRITE = "write"
    NO_WRITE = "no_write"
    SECONDARY_PROPERTY = "secondary_property"
    REQUIRE_APPROVAL = "require_approval"
    PRESERVE_CRM = "preserve_crm"
    MARK_REFRESH = "mark_refresh"


GateOutcome = SyncGateOutcome  # canonical short name used across services


class SyncStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    RETRYING = "retrying"
    FAILED = "failed"
    SKIPPED = "skipped"


class BudgetKind(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class BudgetPeriod(StrEnum):
    DAILY = "daily"
    LIFETIME = "lifetime"


class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class RecordStatus(StrEnum):
    ACTIVE = "active"
    SUPPRESSED = "suppressed"
    MERGED = "merged"
    ARCHIVED = "archived"


class EmailStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    RISKY = "risky"
    UNKNOWN = "unknown"


class CampaignStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class CrmSystem(StrEnum):
    SIMULATOR = "simulator"
    HUBSPOT = "hubspot"
    SALESFORCE = "salesforce"
