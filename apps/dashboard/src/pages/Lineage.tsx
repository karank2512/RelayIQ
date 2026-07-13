import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { get } from '../lib/api'
import type { FieldLineage, LineageObservation } from '../lib/types'
import { PageHeader } from '../components/PageHeader'
import { Badge } from '../components/Badge'
import { ConfidenceBar } from '../components/ConfidenceBar'
import { JsonViewer } from '../components/JsonViewer'
import { ErrorNote, KV } from '../components/misc'
import { fmtAge, fmtCredits, fmtMs, shortId } from '../lib/format'

function ObservationCard({ obs }: { obs: LineageObservation }) {
  return (
    <div className={`obs-card ${obs.is_selected ? 'selected' : ''} ${obs.is_rejected ? 'rejected' : ''}`}>
      <div className="obs-card-head">
        <Badge tone="accent">{obs.provider}</Badge>
        {obs.is_selected && <Badge tone="ok">selected</Badge>}
        {obs.is_rejected && <Badge tone="danger" title={obs.rejection_reason ?? undefined}>rejected</Badge>}
      </div>
      <div className="obs-value">{obs.normalized_value ?? obs.raw_value ?? '—'}</div>
      {obs.raw_value && obs.normalized_value && obs.raw_value !== obs.normalized_value && (
        <div className="faint">raw: {obs.raw_value}</div>
      )}
      <KV
        pairs={[
          ['Cost', fmtCredits(obs.cost_credits)],
          ['Source age', obs.source_timestamp ? fmtAge(obs.source_timestamp) : '—'],
          ['Provider conf.', obs.provider_confidence != null ? obs.provider_confidence.toFixed(2) : '—'],
          ['Internal conf.', obs.internal_confidence != null ? <ConfidenceBar value={obs.internal_confidence} /> : '—'],
          ['Staleness', obs.staleness_state ?? 'unknown'],
        ]}
      />
    </div>
  )
}

export function LineagePage() {
  const { entityType = 'contact', entityId = '', fieldName = '' } = useParams()
  const query = useQuery({
    queryKey: ['lineage', entityType, entityId, fieldName],
    queryFn: () => get<FieldLineage>(`/v1/entities/${entityType}/${entityId}/lineage/${fieldName}`),
  })
  const lin = query.data

  const stages: Array<{ title: string; body: React.ReactNode; count?: number }> = lin
    ? [
        {
          title: 'Routing decisions',
          count: lin.routing_decisions.length,
          body: lin.routing_decisions.map((r) => (
            <div key={r.id} className="stage-item">
              <div>
                <Badge tone="accent">{r.selected_provider ?? 'none'}</Badge>{' '}
                <span className="faint">strategy {r.strategy} · expected {fmtCredits(r.expected_cost)}
                  {r.actual_cost != null ? ` · actual ${fmtCredits(r.actual_cost)}` : ''}
                  {r.fallback_used ? ' · fallback used' : ''}</span>
              </div>
              <JsonViewer data={{ candidates: r.candidates, rejected: r.rejected_providers, factors: r.factors }} label="factors" />
            </div>
          )),
        },
        {
          title: 'Provider calls',
          count: lin.provider_requests.length,
          body: lin.provider_requests.map((p) => (
            <div key={p.id} className="stage-item">
              <Badge tone="accent">{p.provider}</Badge>{' '}
              <Badge tone={p.outcome === 'success' ? 'ok' : 'danger'}>{p.outcome ?? '?'}</Badge>{' '}
              <span className="faint">
                {fmtMs(p.latency_ms)} · {fmtCredits(p.cost_credits)} · retries {p.retry_count}
                {p.error ? ` · ${p.error}` : ''} · trace {shortId(p.trace_id)}
              </span>
            </div>
          )),
        },
        {
          title: 'Observations (normalization + validation)',
          count: lin.observations.length,
          body: (
            <div className="obs-grid">
              {lin.observations.map((o) => (
                <ObservationCard key={o.id} obs={o} />
              ))}
            </div>
          ),
        },
        {
          title: 'Conflict reconciliation',
          count: lin.reconciliations.length,
          body: lin.reconciliations.map((r) => (
            <div key={r.id} className="stage-item">
              <Badge tone={(r.outcome ?? '').includes('accept') ? 'ok' : r.outcome === 'require_review' ? 'warn' : 'neutral'}>
                {r.outcome ?? 'unknown'}
              </Badge>{' '}
              <span className="faint">severity {Number(r.conflict_severity ?? 0).toFixed(2)} · {fmtAge(r.at)}</span>
              <blockquote className="reasoning">{r.reasoning}</blockquote>
              <JsonViewer data={r.factors} label="factors" />
            </div>
          )),
        },
        {
          title: 'Confidence',
          count: lin.confidence_evaluations.length,
          body: lin.confidence_evaluations.map((c) => (
            <div key={c.id} className="stage-item">
              <ConfidenceBar value={c.score} />{' '}
              <span className="faint">{c.level} · {c.formula_version}</span>
              <JsonViewer data={c.components} label="components" />
            </div>
          )),
        },
        {
          title: 'Human review',
          count: lin.review.tasks.length + lin.review.decisions.length,
          body: (
            <>
              {lin.review.tasks.map((t) => (
                <div key={t.id} className="stage-item">
                  <Badge tone={t.status === 'pending' ? 'warn' : 'neutral'}>{t.status}</Badge>{' '}
                  <span className="faint">{t.reason}</span>
                </div>
              ))}
              {lin.review.decisions.map((d) => (
                <div key={d.id} className="stage-item">
                  <Badge tone="accent">{d.action}</Badge>{' '}
                  <span className="faint">
                    by {shortId(d.reviewer_id)} · {fmtAge(d.at)}
                    {d.note ? ` · “${d.note}”` : ''}
                  </span>
                </div>
              ))}
              {lin.review.tasks.length === 0 && <span className="faint">No review needed.</span>}
            </>
          ),
        },
        {
          title: 'CRM sync',
          count: lin.crm_syncs.length,
          body: lin.crm_syncs.length
            ? lin.crm_syncs.map((s) => (
                <div key={s.id} className="stage-item">
                  <Badge tone={s.status === 'success' ? 'ok' : s.status === 'failed' ? 'danger' : 'neutral'}>
                    {s.status}
                  </Badge>{' '}
                  {s.dry_run && <Badge tone="warn">dry run</Badge>}{' '}
                  <span className="faint">{fmtAge(s.at)} · external {s.external_id ?? '—'}</span>
                  <JsonViewer data={s.change} label="before / after / gate" />
                </div>
              ))
            : <span className="faint">Not synced.</span>,
        },
      ]
    : []

  return (
    <>
      <PageHeader
        title={`Lineage — ${fieldName}`}
        subtitle={
          <>
            <Link to={`/entities/${entityType}/${entityId}`}>{entityType} {shortId(entityId)}</Link>
            {' '}· full decision chain from input to CRM
          </>
        }
      />
      {query.isError && <ErrorNote error={query.error} />}
      {lin?.canonical && (
        <section className="panel highlight">
          <KV
            pairs={[
              ['Canonical value', <strong key="v">{lin.canonical.value ?? '—'}</strong>],
              ['Confidence', lin.canonical.confidence != null ? <ConfidenceBar key="c" value={lin.canonical.confidence} /> : '—'],
              ['Staleness', lin.canonical.staleness_state ?? 'unknown'],
              ['Source', lin.canonical.source_kind ?? '—'],
              ['Locked', lin.canonical.locked ? 'yes' : 'no'],
              ['Verified', fmtAge(lin.canonical.last_verified_at)],
            ]}
          />
        </section>
      )}
      <div className="lineage-timeline">
        {stages.map((s) => (
          <section className="lineage-stage" key={s.title}>
            <h3>
              {s.title} {s.count !== undefined && <span className="faint">({s.count})</span>}
            </h3>
            {s.body}
          </section>
        ))}
      </div>
    </>
  )
}
