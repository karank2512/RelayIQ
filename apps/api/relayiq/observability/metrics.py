"""Prometheus metrics. All label sets are bounded-cardinality by construction:
no tenant IDs, entity IDs, or free-form strings as label values."""

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "relayiq_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "relayiq_http_request_seconds", "HTTP request latency", ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

CACHE_OPS = Counter(
    "relayiq_cache_ops_total", "Field cache operations", ["entity_type", "field", "status"]
)

PROVIDER_CALLS = Counter(
    "relayiq_provider_calls_total", "Provider calls", ["provider", "outcome"]
)
PROVIDER_LATENCY = Histogram(
    "relayiq_provider_latency_ms", "Provider call latency (reported, ms)", ["provider"],
    buckets=(25, 50, 100, 200, 400, 800, 1600, 3200, 6400),
)
PROVIDER_COST = Counter(
    "relayiq_provider_cost_credits_total", "Provider credits spent", ["provider"]
)
PROVIDER_RETRIES = Counter(
    "relayiq_provider_retries_total", "Provider retry attempts", ["provider"]
)
CIRCUIT_STATE = Gauge(
    "relayiq_provider_circuit_open", "1 when provider circuit breaker is open", ["provider"]
)

JOBS = Counter("relayiq_enrichment_jobs_total", "Enrichment jobs by terminal status", ["status"])
JOB_DURATION = Histogram(
    "relayiq_enrichment_job_seconds", "Enrichment job wall time", ["status"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
PRE_DECISIONS = Counter("relayiq_pre_decisions_total", "Pre-enrichment decisions", ["decision"])
ROUTING_DECISIONS = Counter(
    "relayiq_routing_decisions_total", "Field routing decisions", ["provider", "strategy"]
)
RECONCILIATIONS = Counter(
    "relayiq_reconciliations_total", "Reconciliation outcomes", ["outcome"]
)
REVIEW_ACTIONS = Counter("relayiq_review_actions_total", "Review actions", ["action"])
CRM_SYNCS = Counter("relayiq_crm_syncs_total", "CRM sync attempts", ["system", "status"])
CRM_GATE = Counter("relayiq_crm_gate_total", "CRM gate outcomes", ["outcome"])
WEBHOOKS = Counter("relayiq_webhooks_total", "Webhook deliveries", ["source", "result"])
BUDGET_BLOCKS = Counter("relayiq_budget_blocks_total", "Requests blocked by budget", ["kind"])
QUEUE_DEPTH = Gauge("relayiq_review_queue_depth", "Pending review tasks")
RATE_LIMITED = Counter(
    "relayiq_rate_limited_total", "Requests rejected by the API rate limiter", ["scope"]
)
