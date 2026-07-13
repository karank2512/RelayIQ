import { useQuery } from '@tanstack/react-query'
import { get } from '../lib/api'
import type { BudgetOut, CampaignEconomics, CampaignOut } from '../lib/types'
import { PageHeader } from '../components/PageHeader'
import { Badge } from '../components/Badge'
import { ErrorNote, KV } from '../components/misc'
import { fmtCredits, fmtNum } from '../lib/format'

function BudgetBar({ b }: { b: BudgetOut }) {
  const used = b.spent_credits + b.reserved_credits
  const pct = b.limit_credits > 0 ? Math.min(1, used / b.limit_credits) : 0
  const warn = pct >= b.warning_threshold
  return (
    <div className="budget-block">
      <div className="budget-head">
        <strong>{b.name}</strong>{' '}
        <Badge tone={b.kind === 'hard' ? 'danger' : 'warn'}>{b.kind}</Badge>{' '}
        <Badge tone="neutral">{b.period}</Badge>
        {warn && <Badge tone="warn" title={`Degradation: ${b.degradation_mode}`}>warning — {b.degradation_mode}</Badge>}
      </div>
      <div
        className="budget-bar"
        role="progressbar"
        aria-valuenow={Math.round(pct * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${b.name} budget usage`}
      >
        <div className={`budget-fill ${warn ? 'warn' : ''}`} style={{ width: `${pct * 100}%` }} />
        <div className="budget-threshold" style={{ left: `${b.warning_threshold * 100}%` }} title="Warning threshold" />
      </div>
      <div className="faint">
        {fmtCredits(b.spent_credits)} spent + {fmtCredits(b.reserved_credits)} reserved of{' '}
        {fmtCredits(b.limit_credits)} · remaining {fmtCredits(b.limit_credits - used)}
      </div>
    </div>
  )
}

function CampaignCard({ c }: { c: CampaignOut }) {
  const econ = useQuery({
    queryKey: ['campaign-economics', c.id],
    queryFn: () => get<CampaignEconomics>(`/v1/metrics/campaigns/${c.id}/economics`),
  })
  const e = econ.data
  return (
    <section className="panel">
      <div className="obs-card-head">
        <h3 style={{ margin: 0 }}>{c.name}</h3>
        <Badge tone={c.status === 'active' ? 'ok' : 'neutral'}>{c.status}</Badge>
        {!c.crm_write_enabled && <Badge tone="warn">CRM writes off</Badge>}
      </div>
      <KV
        pairs={[
          ['Min confidence', c.min_confidence.toFixed(2)],
          ['Required fields', c.required_fields.join(', ') || '—'],
          ['Filters', Object.keys(c.filters).length ? JSON.stringify(c.filters) : 'none'],
        ]}
      />
      {c.budgets.map((b) => <BudgetBar key={b.id} b={b} />)}
      <h4>Economics (measured from the ledger)</h4>
      {e ? (
        <KV
          pairs={[
            ['Attempted / accepted / usable', `${fmtNum(e.attempted_records)} / ${fmtNum(e.accepted_records)} / ${fmtNum(e.usable_leads)}`],
            ['Total spend', fmtCredits(e.total_cost_credits)],
            ['Cost / accepted record', fmtCredits(e.cost_per_accepted_record)],
            ['Cost / usable lead', fmtCredits(e.cost_per_usable_lead)],
            ['Redundant spend avoided', fmtCredits(e.redundant_cost_avoided_credits)],
            ['Blocked by filters', fmtNum(e.enrichment_prevented_by_filters)],
          ]}
        />
      ) : (
        <p className="faint">{econ.isPending ? 'Loading…' : 'No spend yet.'}</p>
      )}
    </section>
  )
}

export function CampaignsPage() {
  const query = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => get<CampaignOut[]>('/v1/admin/campaigns'),
  })
  return (
    <>
      <PageHeader
        title="Campaigns & budgets"
        subtitle="Budgets are reserved atomically before any provider call — concurrent requests can never jointly exceed a hard limit. Past the warning threshold the campaign degrades (cheaper providers, cache-only, or stop)."
      />
      {query.isError && <ErrorNote error={query.error} />}
      <div className="card-grid">
        {(query.data ?? []).map((c) => <CampaignCard key={c.id} c={c} />)}
        {query.data?.length === 0 && <p className="faint">No campaigns yet.</p>}
      </div>
    </>
  )
}
