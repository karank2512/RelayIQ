import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { get } from '../lib/api'
import type { CostMetrics, QualityMetrics } from '../lib/types'
import { PageHeader } from '../components/PageHeader'
import { StatCard } from '../components/StatCard'
import { Tabs } from '../components/Tabs'
import { DataTable } from '../components/DataTable'
import type { Column } from '../components/DataTable'
import { ErrorNote } from '../components/misc'
import { fmtCredits, fmtNum, fmtPct } from '../lib/format'

const CHART_STYLE = { fontSize: 12 }

function SpendChart({ data, title }: { data: Array<{ key: string | null; spend_credits: number }>; title: string }) {
  const rows = data.filter((d) => d.key).map((d) => ({ name: d.key as string, credits: d.spend_credits }))
  if (!rows.length) return <p className="faint">No spend recorded for {title.toLowerCase()}.</p>
  return (
    <div className="chart-panel">
      <h4>{title}</h4>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={rows} style={CHART_STYLE}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="name" stroke="var(--text-faint)" />
          <YAxis stroke="var(--text-faint)" />
          <Tooltip contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)' }} />
          <Bar dataKey="credits" fill="var(--accent)" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function CostTab() {
  const q = useQuery({ queryKey: ['metrics', 'cost'], queryFn: () => get<CostMetrics>('/v1/metrics/cost') })
  if (q.isError) return <ErrorNote error={q.error} />
  const m = q.data
  return (
    <>
      <div className="stat-grid">
        <StatCard label="Total spend" value={fmtCredits(m?.total_cost_credits)} hint="Sum of actual credits across every ledger entry." />
        <StatCard label="Cost / attempted" value={fmtCredits(m?.cost_per_attempted_record)} hint="Total spend ÷ jobs that reached the pipeline." />
        <StatCard label="Cost / accepted" value={fmtCredits(m?.cost_per_accepted_record)} hint="Total spend ÷ jobs whose result was accepted." />
        <StatCard label="Cost / usable lead" value={fmtCredits(m?.cost_per_usable_lead)} hint="Total spend ÷ leads meeting the configurable usable-lead definition." />
        <StatCard label="Redundant spend avoided" value={fmtCredits(m?.redundant_cost_avoided_credits)} hint="Provider cost that cache hits and idempotent replays did NOT spend — measured, not estimated." />
        <StatCard label="Spend on stale results" value={fmtCredits(m?.spend_on_stale_credits)} hint="Credits paid for values older than the staleness policy at retrieval time." />
      </div>
      <div className="chart-row">
        <SpendChart data={m?.by_provider ?? []} title="Spend by provider" />
        <SpendChart data={m?.by_field ?? []} title="Spend by field" />
      </div>
    </>
  )
}

function QualityTab() {
  const q = useQuery({ queryKey: ['metrics', 'quality'], queryFn: () => get<QualityMetrics>('/v1/metrics/quality') })
  if (q.isError) return <ErrorNote error={q.error} />
  const m = q.data
  const stalenessRows = Object.entries(m?.staleness_distribution ?? {}).map(([name, count]) => ({ name, count }))
  const pfCols: Column<QualityMetrics['provider_field_quality'][number]>[] = [
    { key: 'provider', header: 'Provider', render: (r) => r.provider, sortValue: (r) => r.provider },
    { key: 'field', header: 'Field', render: (r) => <code>{r.field}</code>, sortValue: (r) => r.field },
    { key: 'obs', header: 'Observations', align: 'right', render: (r) => fmtNum(r.observations), sortValue: (r) => r.observations },
    { key: 'sel', header: 'Selected share', align: 'right', render: (r) => fmtPct(r.selected_share), sortValue: (r) => r.selected_share },
    { key: 'rej', header: 'Rejected share', align: 'right', render: (r) => fmtPct(r.rejected_share), sortValue: (r) => r.rejected_share },
  ]
  return (
    <>
      <div className="stat-grid">
        <StatCard label="Fill rate" value={fmtPct(m?.fill_rate)} hint="Fields filled ÷ fields requested on enriched jobs." />
        <StatCard label="Conflict rate" value={fmtPct(m?.conflict_rate)} hint="Reconciliations that found disagreement ÷ all reconciliations." />
        <StatCard label="Stale share" value={fmtPct(m?.stale_share)} hint="Canonical fields currently stale or expired." />
        <StatCard label="Usable leads" value={fmtNum(m?.usable_leads)} hint="Leads meeting the configurable usable-lead definition." />
        <StatCard label="CRM sync failure rate" value={fmtPct(m?.crm_sync_failure_rate)} hint="Failed sync attempts ÷ all attempts." />
        <StatCard label="Observations stored" value={fmtNum(m?.observations)} hint="Provider observations are never overwritten — every value is preserved for lineage." />
      </div>
      <div className="chart-row">
        <div className="chart-panel">
          <h4>Canonical field staleness</h4>
          {stalenessRows.length ? (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={stalenessRows} style={CHART_STYLE}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="name" stroke="var(--text-faint)" />
                <YAxis stroke="var(--text-faint)" />
                <Tooltip contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)' }} />
                <Bar dataKey="count" fill="var(--ok)" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <p className="faint">No canonical fields yet.</p>}
        </div>
        <div className="chart-panel">
          <h4>Reconciliation outcomes</h4>
          {Object.keys(m?.reconciliation_outcomes ?? {}).length ? (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={Object.entries(m?.reconciliation_outcomes ?? {}).map(([name, count]) => ({ name, count }))} style={CHART_STYLE}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="name" stroke="var(--text-faint)" />
                <YAxis stroke="var(--text-faint)" />
                <Tooltip contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)' }} />
                <Bar dataKey="count" fill="var(--warn)" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <p className="faint">No reconciliations yet.</p>}
        </div>
      </div>
      <h3 className="section-title">Provider × field performance</h3>
      <DataTable
        columns={pfCols}
        rows={m?.provider_field_quality}
        rowKey={(r) => `${r.provider}:${r.field}`}
        loading={q.isPending}
        emptyText="No observations yet."
      />
    </>
  )
}

export function AnalyticsPage() {
  const [tab, setTab] = useState<'cost' | 'quality'>('cost')
  return (
    <>
      <PageHeader
        title="Analytics"
        subtitle="Every number on this page is derived from persisted ledger and decision rows — nothing is hardcoded. Providers are simulators, so credit figures are synthetic economics."
      />
      <Tabs
        tabs={[{ id: 'cost', label: 'Cost' }, { id: 'quality', label: 'Data quality' }]}
        active={tab}
        onChange={setTab}
      />
      {tab === 'cost' ? <CostTab /> : <QualityTab />}
    </>
  )
}
