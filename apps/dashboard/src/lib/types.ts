/** API response shapes, mirrored from apps/api/relayiq routers & schemas. */

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

// ── Enrichment ───────────────────────────────────────────────

export interface JobOut {
  id: string
  status: string
  entity_type: string
  entity_id: string | null
  pre_decision: string | null
  decision_reasons: unknown[]
  requested_fields: string[]
  estimated_cost_credits: number
  actual_cost_credits: number
  result_summary: Record<string, unknown>
  error: string | null
  trace_id: string | null
  batch_id: string | null
  dry_run: boolean
  created_at: string | null
  finished_at: string | null
}

export const CONTACT_FIELDS = [
  'first_name',
  'last_name',
  'full_name',
  'work_email',
  'job_title',
  'seniority',
  'department',
  'country',
  'linkedin_url',
] as const

export const ACCOUNT_FIELDS = [
  'name',
  'website',
  'root_domain',
  'linkedin_url',
  'industry',
  'sub_industry',
  'employee_count',
  'annual_revenue_usd',
  'hq_city',
  'hq_state',
  'hq_country',
  'company_type',
  'founded_year',
  'technology_signals',
] as const

export const JOB_STATUSES = [
  'received',
  'queued',
  'running',
  'awaiting_review',
  'completed',
  'completed_cached',
  'rejected',
  'skipped',
  'blocked_budget',
  'blocked_policy',
  'partial',
  'failed',
] as const

// ── Entities ─────────────────────────────────────────────────

export interface AccountRow {
  id: string
  external_crm_id: string | null
  name: string | null
  normalized_name: string | null
  website: string | null
  root_domain: string | null
  linkedin_url: string | null
  industry: string | null
  sub_industry: string | null
  employee_count: number | null
  employee_range: string | null
  annual_revenue_usd: number | null
  hq_city: string | null
  hq_state: string | null
  hq_country: string | null
  company_type: string | null
  founded_year: number | null
  technology_signals: unknown
  record_status: string | null
  record_confidence: number | null
  last_verified_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ContactRow {
  id: string
  external_crm_id: string | null
  first_name: string | null
  last_name: string | null
  full_name: string | null
  work_email: string | null
  email_status: string | null
  job_title: string | null
  normalized_job_title: string | null
  seniority: string | null
  department: string | null
  account_id: string | null
  company_name: string | null
  company_domain: string | null
  country: string | null
  linkedin_url: string | null
  record_status: string | null
  record_confidence: number | null
  last_verified_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface CanonicalField {
  field_name: string
  value: string | null
  normalized_value: string | null
  confidence: number | null
  staleness_state: string | null
  source_kind: string | null
  locked: boolean
  selected_observation_id: string | null
  last_verified_at: string | null
}

export interface EntityDetail {
  entity: Record<string, unknown>
  canonical_fields: CanonicalField[]
}

// ── Lineage ──────────────────────────────────────────────────

export interface LineageObservation {
  id: string
  provider: string
  raw_value: string | null
  normalized_value: string | null
  provider_confidence: number | null
  internal_confidence: number | null
  cost_credits: number
  latency_ms?: number | null
  staleness_state: string | null
  validation?: unknown
  is_selected: boolean
  is_rejected: boolean
  rejection_reason?: string | null
  review_status?: string | null
  source_timestamp?: string | null
  retrieved_at?: string | null
  trace_id?: string | null
  job_id?: string | null
  field_name?: string
}

export interface FieldLineage {
  entity_type: string
  entity_id: string
  field_name: string
  canonical: {
    value: string | null
    normalized_value: string | null
    confidence: number | null
    staleness_state: string | null
    source_kind: string | null
    selected_observation_id: string | null
    locked: boolean
    last_verified_at: string | null
  } | null
  routing_decisions: Array<{
    id: string
    job_id: string | null
    strategy: string | null
    selected_provider: string | null
    candidates: unknown
    rejected_providers: unknown
    factors: unknown
    expected_cost: number
    actual_cost: number | null
    fallback_used: boolean
    at: string | null
  }>
  provider_requests: Array<{
    id: string
    provider: string
    outcome: string | null
    retry_count: number
    latency_ms: number | null
    cost_credits: number
    error: string | null
    trace_id: string | null
    at: string | null
  }>
  observations: LineageObservation[]
  reconciliations: Array<{
    id: string
    outcome: string | null
    chosen_value: string | null
    chosen_observation_id: string | null
    reasoning: string | null
    factors: unknown
    conflict_severity: string | null
    at: string | null
  }>
  confidence_evaluations: Array<{
    id: string
    level: string | null
    score: number | null
    components: unknown
    formula_version: string | null
    at: string | null
  }>
  review: {
    tasks: Array<{
      id: string
      status: string
      reason: string
      confidence: number | null
      suggested_value: string | null
      at: string | null
    }>
    decisions: Array<{
      id: string
      task_id: string
      action: string
      reviewer_id: string
      corrected_value: string | null
      note: string | null
      previous_state: unknown
      reverses_decision_id: string | null
      at: string | null
    }>
  }
  crm_syncs: Array<{
    id: string
    status: string
    dry_run: boolean
    change: unknown
    external_id: string | null
    at: string | null
  }>
}

// ── Review ───────────────────────────────────────────────────

export interface ReviewTaskOut {
  id: string
  entity_type: string
  entity_id: string
  field_name: string | null
  reason: string
  status: string
  priority: number
  confidence: number | null
  suggested_value: string | null
  suggested_observation_id: string | null
  job_id: string | null
  created_at: string | null
  resolved_at: string | null
}

export interface ReviewDecisionOut {
  id: string
  task_id: string
  action: string
  reviewer_id: string
  corrected_value: string | null
  note: string | null
  previous_state: Record<string, unknown>
  reverses_decision_id: string | null
  created_at: string | null
}

export interface ReviewTaskDetail {
  task: ReviewTaskOut
  entity: Record<string, unknown> | null
  observations: LineageObservation[]
  decisions: ReviewDecisionOut[]
  lineage: FieldLineage | null
}

// ── Metrics ──────────────────────────────────────────────────

export interface OverviewMetrics {
  records_processed: number
  accepted_records: number
  usable_leads: number
  total_cost_credits: number
  cost_per_usable_lead: number | null
  cost_per_accepted_record: number | null
  fill_rate: number | null
  cache_hit_rate: number | null
  redundant_call_rate: number | null
  redundant_cost_avoided_credits: number
  conflict_rate: number | null
  review_acceptance_rate: number | null
  review_pending: number
  p50_provider_latency_ms: number | null
  p95_provider_latency_ms: number | null
  crm_sync_failure_rate: number | null
  spend_on_rejected_records_credits: number
  spend_on_stale_credits: number
}

export interface SpendBucket {
  key: string
  spend_credits: number
  entries?: number
}

export interface CostMetrics {
  total_cost_credits: number
  attempted_records: number
  accepted_records: number
  complete_records: number
  usable_leads: number
  cost_per_attempted_record: number | null
  cost_per_accepted_record: number | null
  cost_per_complete_record: number | null
  cost_per_usable_lead: number | null
  total_spend_credits: number
  redundant_cost_avoided_credits: number
  redundant_spend_credits: number
  spend_on_stale_credits: number
  spend_on_rejected_records_credits: number
  ledger_entries: number
  by_provider: SpendBucket[]
  by_campaign: SpendBucket[]
  by_operation: SpendBucket[]
  by_field: SpendBucket[]
}

export interface QualityMetrics {
  observations: number
  jobs_total: number
  jobs_enriched: number
  fill_rate: number | null
  conflict_rate: number | null
  reconciliation_outcomes: Record<string, number>
  staleness_distribution: Record<string, number>
  stale_share: number | null
  usable_leads: number
  crm_sync_attempts: number
  crm_sync_failure_rate: number | null
  crm_sync_outcomes: Record<string, number>
  provider_field_quality: Array<{
    provider: string
    field: string
    observations: number
    selected_share: number | null
    rejected_share: number | null
  }>
}

// ── Admin ────────────────────────────────────────────────────

export interface ProviderStats {
  provider: string
  requests: number
  success_rate?: number
  temp_fail_rate?: number
  perm_fail_rate?: number
  timeout_rate?: number
  rate_limited?: number
  p50_latency_ms?: number | null
  p95_latency_ms?: number | null
  p99_latency_ms?: number | null
  cost_credits?: number
}

export interface ProviderInfo {
  id: string
  key: string
  display_name: string
  adapter: string
  version: string | null
  enabled: boolean
  timeout_ms: number
  max_retries: number
  reliability_prior: number
  rate_limit_per_minute: number | null
  simulation: string | null
  circuit_state: string
  capabilities: Record<string, string[]>
  stats_24h: ProviderStats
}

export interface RoutingPolicyOut {
  id: string
  name: string
  version: number
  is_active: boolean
  document: Record<string, unknown>
  created_at: string | null
}

export interface StalenessPolicyOut {
  id: string
  tenant_id: string | null
  entity_type: string
  field_name: string
  fresh_days: number
  aging_days: number
  stale_days: number
  scope: 'tenant' | 'global'
}

export interface BudgetOut {
  id: string
  name: string
  kind: string
  period: string
  limit_credits: number
  spent_credits: number
  reserved_credits: number
  warning_threshold: number
  degradation_mode: string
  is_active: boolean
}

export interface CampaignOut {
  id: string
  name: string
  status: string
  filters: Record<string, unknown>
  required_fields: string[]
  min_confidence: number
  crm_write_enabled: boolean
  routing_policy_id: string | null
  budgets: BudgetOut[]
}

export interface CampaignEconomics extends CostMetricsBase {
  budgets: Array<{
    id: string
    name: string
    kind: string
    period: string
    limit_credits: number
    spent_credits: number
    reserved_credits: number
    remaining_credits: number
    variance_credits: number
    warning_threshold: number
  }>
  enrichment_prevented_by_filters: number
}

export interface CostMetricsBase {
  total_cost_credits: number
  attempted_records: number
  accepted_records: number
  complete_records: number
  usable_leads: number
  cost_per_attempted_record: number | null
  cost_per_accepted_record: number | null
  cost_per_complete_record: number | null
  cost_per_usable_lead: number | null
  total_spend_credits: number
  redundant_cost_avoided_credits: number
  redundant_spend_credits: number
  spend_on_stale_credits: number
  spend_on_rejected_records_credits: number
  ledger_entries: number
}

// ── CRM ──────────────────────────────────────────────────────

export interface CrmFieldChange {
  before?: unknown
  after?: unknown
  gate?: string
  outcome?: string
  reasons?: string[]
  [key: string]: unknown
}

export interface CrmSyncAttemptRow {
  id: string
  entity_type: string
  entity_id: string
  external_id: string | null
  status: string
  dry_run: boolean
  field_changes: Record<string, CrmFieldChange> | null
  gate_summary: Record<string, unknown> | null
  error: string | null
  synced_at: string | null
  created_at: string | null
}

export interface CrmSimRecordRow {
  id: string
  object_type: string
  external_id: string
  properties: Record<string, unknown>
  property_updated_at: Record<string, string> | null
  updated_at: string | null
}

// ── Audit ────────────────────────────────────────────────────

export interface AuditEventRow {
  id: string
  action: string
  object_type: string | null
  object_id: string | null
  actor_user_id: string | null
  actor_type: string | null
  before: unknown
  after: unknown
  trace_id: string | null
  created_at: string | null
}
