import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { get, post, put } from '../lib/api'
import type { RoutingPolicyOut, StalenessPolicyOut } from '../lib/types'
import { PageHeader } from '../components/PageHeader'
import { DataTable } from '../components/DataTable'
import type { Column } from '../components/DataTable'
import { Badge } from '../components/Badge'
import { JsonViewer } from '../components/JsonViewer'
import { Tabs } from '../components/Tabs'
import { ErrorNote, errorMessage } from '../components/misc'
import { useAuth } from '../lib/auth'

const EXAMPLE_YAML = `version: 1
defaults:
  strategy: balanced      # cheapest_capable | quality_first | balanced | dynamic
  fallback: true
  max_candidates: 3
fields:
  contact.job_title: {providers: [beta, alpha], strategy: quality_first}
  account.root_domain: {providers: [alpha, beta], strategy: cheapest_capable}
`

function RoutingTab() {
  const { hasRole } = useAuth()
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [yamlDoc, setYamlDoc] = useState(EXAMPLE_YAML)
  const query = useQuery({
    queryKey: ['routing-policies'],
    queryFn: () => get<RoutingPolicyOut[]>('/v1/admin/routing-policies'),
  })
  const create = useMutation({
    mutationFn: () => post('/v1/admin/routing-policies', { name, yaml_document: yamlDoc, activate: true }),
    onSuccess: () => { setName(''); void qc.invalidateQueries({ queryKey: ['routing-policies'] }) },
  })

  return (
    <>
      {query.isError && <ErrorNote error={query.error} />}
      <div className="panel">
        {(query.data ?? []).length === 0 && (
          <p className="faint">
            No tenant policies — the built-in default routes company fields to Alpha
            (cheapest capable) and people fields to Beta (quality first).
          </p>
        )}
        {(query.data ?? []).map((p) => (
          <div key={p.id} className="stage-item">
            <strong>{p.name}</strong> <Badge tone={p.is_active ? 'ok' : 'neutral'}>{p.is_active ? 'active' : 'inactive'}</Badge>{' '}
            <span className="faint">v{p.version}</span>
            <JsonViewer data={p.document} label="policy document" />
          </div>
        ))}
      </div>
      {hasRole('operator') && (
        <section className="panel">
          <h3>Create or update a routing policy (YAML)</h3>
          {create.isError && <div className="error-note" role="alert">{errorMessage(create.error)}</div>}
          <label className="field-label" htmlFor="policy-name">Policy name</label>
          <input id="policy-name" className="input" value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="e.g. q3-outbound" />
          <label className="field-label" htmlFor="policy-yaml">Policy document</label>
          <textarea id="policy-yaml" className="input mono" rows={12} value={yamlDoc}
                    onChange={(e) => setYamlDoc(e.target.value)} spellCheck={false} />
          <button type="button" className="btn primary" disabled={!name || create.isPending}
                  onClick={() => create.mutate()}>
            Save & activate
          </button>
        </section>
      )}
    </>
  )
}

function StalenessTab() {
  const { hasRole } = useAuth()
  const qc = useQueryClient()
  const [form, setForm] = useState({ entity_type: 'contact', field_name: 'job_title', fresh_days: 30, aging_days: 60, stale_days: 90 })
  const query = useQuery({
    queryKey: ['staleness-policies'],
    queryFn: () => get<StalenessPolicyOut[]>('/v1/admin/staleness-policies'),
  })
  const upsert = useMutation({
    mutationFn: () => put('/v1/admin/staleness-policies', form),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['staleness-policies'] }),
  })

  const cols: Column<StalenessPolicyOut>[] = [
    { key: 'scope', header: 'Scope', render: (r) => <Badge tone={r.scope === 'tenant' ? 'accent' : 'neutral'}>{r.scope}</Badge> },
    { key: 'entity', header: 'Entity', render: (r) => r.entity_type },
    { key: 'field', header: 'Field', render: (r) => <code>{r.field_name}</code>, sortValue: (r) => r.field_name },
    { key: 'fresh', header: 'Fresh ≤', align: 'right', render: (r) => `${r.fresh_days}d` },
    { key: 'aging', header: 'Aging ≤', align: 'right', render: (r) => `${r.aging_days}d` },
    { key: 'stale', header: 'Stale ≤', align: 'right', render: (r) => `${r.stale_days}d (then expired)` },
  ]

  return (
    <>
      {query.isError && <ErrorNote error={query.error} />}
      <p className="faint">
        Staleness drives cache reuse, routing, confidence decay, CRM gating, and review
        priority. Fields not listed use built-in defaults (e.g. job title 30/60/90 days).
      </p>
      <DataTable<StalenessPolicyOut>
        columns={cols}
        rows={query.data}
        rowKey={(r) => r.id}
        loading={query.isPending}
        emptyText="No overrides — built-in defaults apply."
      />
      {hasRole('operator') && (
        <section className="panel">
          <h3>Set a tenant override</h3>
          {upsert.isError && <div className="error-note" role="alert">{errorMessage(upsert.error)}</div>}
          <div className="btn-row wrap">
            <label className="field-label" htmlFor="sp-entity">Entity
              <select id="sp-entity" className="input" value={form.entity_type}
                      onChange={(e) => setForm({ ...form, entity_type: e.target.value })}>
                <option value="contact">contact</option>
                <option value="account">account</option>
              </select>
            </label>
            <label className="field-label" htmlFor="sp-field">Field
              <input id="sp-field" className="input" value={form.field_name}
                     onChange={(e) => setForm({ ...form, field_name: e.target.value })} />
            </label>
            {(['fresh_days', 'aging_days', 'stale_days'] as const).map((k) => (
              <label key={k} className="field-label" htmlFor={`sp-${k}`}>{k.replace('_', ' ')}
                <input id={`sp-${k}`} className="input" type="number" min={1} value={form[k]}
                       onChange={(e) => setForm({ ...form, [k]: Number(e.target.value) })} />
              </label>
            ))}
            <button type="button" className="btn primary" disabled={upsert.isPending}
                    onClick={() => upsert.mutate()}>
              Save override
            </button>
          </div>
        </section>
      )}
    </>
  )
}

export function PoliciesPage() {
  const [tab, setTab] = useState<'routing' | 'staleness'>('routing')
  return (
    <>
      <PageHeader
        title="Policies"
        subtitle="Routing policies decide which provider answers each field; staleness policies decide when a value stops being trusted."
      />
      <Tabs
        tabs={[{ id: 'routing', label: 'Routing' }, { id: 'staleness', label: 'Staleness' }]}
        active={tab}
        onChange={setTab}
      />
      {tab === 'routing' ? <RoutingTab /> : <StalenessTab />}
    </>
  )
}
