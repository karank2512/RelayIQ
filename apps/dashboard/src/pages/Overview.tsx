import { useQuery } from '@tanstack/react-query'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { get } from '../lib/api'
import type { CostMetrics, OverviewMetrics } from '../lib/types'
import { PageHeader } from '../components/PageHeader'
import { StatCard } from '../components/StatCard'
import { ErrorNote } from '../components/misc'
import { fmtCredits, fmtMs, fmtNum, fmtPct } from '../lib/format'

export function OverviewPage() {
  const overview = useQuery({
    queryKey: ['metrics', 'overview'],
    queryFn: () => get<OverviewMetrics>('/v1/metrics/overview'),
  })
  const cost = useQuery({
    queryKey: ['metrics', 'cost'],
    queryFn: () => get<CostMetrics>('/v1/metrics/cost'),
  })

  const m = overview.data

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="Primary product metrics across the tenant. All numbers are derived from persisted decision records — providers in this environment are simulators."
      />
      {overview.isError && <ErrorNote error={overview.error} />}
      <div className="stat-grid">
        <StatCard
          label="Cost / usable lead"
          value={overview.isPending ? '…' : fmtCredits(m?.cost_per_usable_lead)}
          hint="Total enrichment spend divided by the number of jobs whose entity ended up satisfying the usable-lead definition (required fields filled at sufficient confidence)."
          sub={m ? `${fmtNum(m.usable_leads)} usable leads` : undefined}
        />
        <StatCard
          label="Fill rate"
          value={overview.isPending ? '…' : fmtPct(m?.fill_rate)}
          hint="Fields actually filled divided by fields requested, over jobs where the decision engine chose to enrich."
        />
        <StatCard
          label="Redundant-call rate"
          value={overview.isPending ? '…' : fmtPct(m?.redundant_call_rate)}
          hint="Share of per-field ledger entries served from cache (hit or stale hit) instead of a fresh paid provider call. Higher is cheaper."
          sub={m ? `${fmtCredits(m.redundant_cost_avoided_credits)} avoided` : undefined}
        />
        <StatCard
          label="Conflict rate"
          value={overview.isPending ? '…' : fmtPct(m?.conflict_rate)}
          hint="Share of reconciliation decisions that hit a conflict: require_review, accept_with_warning, or retain_crm outcomes divided by all reconciliations."
        />
        <StatCard
          label="Review acceptance"
          value={overview.isPending ? '…' : fmtPct(m?.review_acceptance_rate)}
          hint="Of resolved review tasks, the share where the reviewer accepted the suggested value (as opposed to overriding or rejecting it)."
          sub={m ? `${fmtNum(m.review_pending)} pending` : undefined}
        />
        <StatCard
          label="p95 provider latency"
          value={overview.isPending ? '…' : fmtMs(m?.p95_provider_latency_ms)}
          hint="95th percentile of recorded provider request latency (simulated providers). p50 shown below."
          sub={m ? `p50 ${fmtMs(m.p50_provider_latency_ms)}` : undefined}
        />
        <StatCard
          label="CRM sync failure rate"
          value={overview.isPending ? '…' : fmtPct(m?.crm_sync_failure_rate)}
          hint="Failed CRM sync attempts divided by all sync attempts (simulator CRM in this environment)."
        />
      </div>
      <div className="stat-grid">
        <StatCard
          label="Records processed"
          value={overview.isPending ? '…' : fmtNum(m?.records_processed)}
          hint="Enrichment jobs that reached the pipeline (any status beyond received)."
        />
        <StatCard
          label="Records accepted"
          value={overview.isPending ? '…' : fmtNum(m?.accepted_records)}
          hint="Jobs whose result was auto-accepted or review-accepted."
        />
        <StatCard
          label="Usable leads"
          value={overview.isPending ? '…' : fmtNum(m?.usable_leads)}
          hint="Jobs whose entity satisfies the usable-lead definition stamped on the job result."
        />
        <StatCard
          label="Total spend"
          value={overview.isPending ? '…' : fmtCredits(m?.total_cost_credits)}
          hint="Sum of actual credits spent across attempted jobs."
          sub={
            m
              ? `${fmtCredits(m.spend_on_rejected_records_credits)} on rejected · ${fmtCredits(m.spend_on_stale_credits)} on stale`
              : undefined
          }
        />
      </div>

      <div className="panel">
        <div className="panel-header">Spend by provider</div>
        <div className="panel-body chart-panel">
          {cost.isError && <ErrorNote error={cost.error} />}
          {cost.isPending && <div className="skeleton" style={{ width: '60%' }} />}
          {cost.data && cost.data.by_provider.length === 0 && (
            <div className="table-empty">No provider spend recorded yet.</div>
          )}
          {cost.data && cost.data.by_provider.length > 0 && (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={cost.data.by_provider} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--border)" vertical={false} />
                <XAxis dataKey="key" stroke="var(--text-faint)" fontSize={11} tickLine={false} />
                <YAxis stroke="var(--text-faint)" fontSize={11} tickLine={false} axisLine={false} />
                <Tooltip
                  cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                  contentStyle={{
                    background: 'var(--panel-2)',
                    border: '1px solid var(--border-strong)',
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                  labelStyle={{ color: 'var(--text)' }}
                  formatter={(value) => [fmtCredits(Number(value)), 'spend']}
                />
                <Bar dataKey="spend_credits" fill="var(--accent)" radius={[3, 3, 0, 0]} maxBarSize={48} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </>
  )
}
