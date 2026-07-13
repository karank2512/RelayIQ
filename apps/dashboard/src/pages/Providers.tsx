import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { get, patch } from '../lib/api'
import type { ProviderInfo } from '../lib/types'
import { PageHeader } from '../components/PageHeader'
import { Badge } from '../components/Badge'
import { ErrorNote, KV, errorMessage } from '../components/misc'
import { useAuth } from '../lib/auth'
import { fmtCredits, fmtMs, fmtPct } from '../lib/format'

function circuitTone(state: string): 'ok' | 'warn' | 'danger' {
  if (state === 'closed') return 'ok'
  if (state === 'half_open') return 'warn'
  return 'danger'
}

export function ProvidersPage() {
  const { hasRole } = useAuth()
  const qc = useQueryClient()
  const query = useQuery({
    queryKey: ['providers'],
    queryFn: () => get<ProviderInfo[]>('/v1/admin/providers'),
  })
  const toggle = useMutation({
    mutationFn: (p: ProviderInfo) => patch(`/v1/admin/providers/${p.key}`, { enabled: !p.enabled }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['providers'] }),
  })

  return (
    <>
      <PageHeader
        title="Providers"
        subtitle="Enrichment provider adapters. Both providers in this environment are deterministic simulators — behavior is configurable, spend is synthetic credits."
      />
      {query.isError && <ErrorNote error={query.error} />}
      {toggle.isError && <div className="error-note" role="alert">{errorMessage(toggle.error)}</div>}
      <div className="card-grid">
        {(query.data ?? []).map((p) => {
          const s = p.stats_24h
          return (
            <section key={p.key} className="panel provider-card">
              <div className="obs-card-head">
                <h3 style={{ margin: 0 }}>{p.display_name}</h3>
                {p.simulation && <Badge tone="warn">SIMULATED</Badge>}
                <Badge tone={p.enabled ? 'ok' : 'neutral'}>{p.enabled ? 'enabled' : 'disabled'}</Badge>
                <Badge tone={circuitTone(p.circuit_state)} title="Circuit breaker state">
                  circuit {p.circuit_state}
                </Badge>
              </div>
              <KV
                pairs={[
                  ['Adapter', <code key="a">{p.adapter}</code>],
                  ['Reliability prior', p.reliability_prior.toFixed(2)],
                  ['Timeout', fmtMs(p.timeout_ms)],
                  ['Max retries', p.max_retries],
                  ['Rate limit', p.rate_limit_per_minute ? `${p.rate_limit_per_minute}/min` : 'none'],
                ]}
              />
              <h4>Last 24h (measured)</h4>
              {s && s.requests > 0 ? (
                <KV
                  pairs={[
                    ['Requests', s.requests],
                    ['Success rate', fmtPct(s.success_rate)],
                    ['p50 / p95 latency', `${fmtMs(s.p50_latency_ms)} / ${fmtMs(s.p95_latency_ms)}`],
                    ['Timeouts', fmtPct(s.timeout_rate)],
                    ['Credits spent', fmtCredits(s.cost_credits)],
                  ]}
                />
              ) : (
                <p className="faint">No calls in the last 24 hours.</p>
              )}
              <h4>Capabilities</h4>
              {Object.entries(p.capabilities ?? {}).map(([et, fields]) => (
                <p key={et} className="faint" style={{ margin: '2px 0' }}>
                  <strong>{et}:</strong> {(fields as string[]).join(', ')}
                </p>
              ))}
              {hasRole('admin') && (
                <button
                  type="button"
                  className="btn small"
                  disabled={toggle.isPending}
                  onClick={() => toggle.mutate(p)}
                >
                  {p.enabled ? 'Disable provider' : 'Enable provider'}
                </button>
              )}
            </section>
          )
        })}
      </div>
    </>
  )
}
