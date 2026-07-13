import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { get, post } from '../lib/api'
import type { CampaignOut, JobOut, Page } from '../lib/types'
import { ACCOUNT_FIELDS, CONTACT_FIELDS, JOB_STATUSES } from '../lib/types'
import { PageHeader } from '../components/PageHeader'
import { DataTable, Pagination } from '../components/DataTable'
import type { Column } from '../components/DataTable'
import { Drawer } from '../components/Drawer'
import { StatusBadge, Badge } from '../components/Badge'
import { JsonViewer } from '../components/JsonViewer'
import { ErrorNote, KV, errorMessage } from '../components/misc'
import { fmtCredits, fmtDate, shortId } from '../lib/format'
import { useAuth } from '../lib/auth'

const PAGE_SIZE = 50

export function RequestsPage() {
  const { hasRole } = useAuth()
  const [statusFilter, setStatusFilter] = useState('')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<JobOut | null>(null)
  const [showNew, setShowNew] = useState(false)

  const jobs = useQuery({
    queryKey: ['jobs', statusFilter, offset],
    queryFn: () =>
      get<Page<JobOut>>('/v1/enrichment/jobs', {
        status: statusFilter || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
  })

  const columns: Column<JobOut>[] = [
    {
      key: 'id',
      header: 'Job',
      render: (j) => <span className="mono">{shortId(j.id)}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      render: (j) => <StatusBadge status={j.status} />,
      sortValue: (j) => j.status,
    },
    {
      key: 'entity_type',
      header: 'Entity',
      render: (j) => (
        <span>
          {j.entity_type}{' '}
          {j.entity_id && (
            <Link
              to={`/entities/${j.entity_type}/${j.entity_id}`}
              className="mono"
              onClick={(e) => e.stopPropagation()}
            >
              {shortId(j.entity_id)}
            </Link>
          )}
        </span>
      ),
    },
    {
      key: 'fields',
      header: 'Requested fields',
      render: (j) => <span className="muted">{j.requested_fields.join(', ') || '—'}</span>,
    },
    {
      key: 'pre_decision',
      header: 'Decision',
      render: (j) => (j.pre_decision ? <StatusBadge status={j.pre_decision} /> : <span className="faint">—</span>),
      sortValue: (j) => j.pre_decision ?? '',
    },
    {
      key: 'cost',
      header: 'Cost',
      align: 'right',
      render: (j) => <span className="nowrap">{fmtCredits(j.actual_cost_credits)}</span>,
      sortValue: (j) => j.actual_cost_credits,
    },
    {
      key: 'dry_run',
      header: '',
      render: (j) => (j.dry_run ? <Badge tone="accent">dry-run</Badge> : null),
    },
    {
      key: 'created_at',
      header: 'Created',
      render: (j) => <span className="nowrap muted">{fmtDate(j.created_at)}</span>,
      sortValue: (j) => j.created_at ?? '',
    },
  ]

  return (
    <>
      <PageHeader
        title="Enrichment requests"
        subtitle="Every enrichment job with its pre-decision, cost, and result. Click a row for the full decision trail."
        actions={
          hasRole('operator') ? (
            <button type="button" className="btn primary" onClick={() => setShowNew(true)}>
              New enrichment
            </button>
          ) : undefined
        }
      />
      <div className="filter-bar">
        <label htmlFor="job-status-filter">Status</label>
        <select
          id="job-status-filter"
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value)
            setOffset(0)
          }}
        >
          <option value="">All statuses</option>
          {JOB_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {jobs.data && <span className="muted">{jobs.data.total.toLocaleString()} jobs</span>}
      </div>
      <div className="panel">
        <DataTable
          columns={columns}
          rows={jobs.data?.items}
          rowKey={(j) => j.id}
          loading={jobs.isPending}
          error={jobs.isError ? errorMessage(jobs.error) : null}
          emptyText="No enrichment jobs match this filter."
          onRowClick={setSelected}
          footer={
            jobs.data ? (
              <Pagination
                total={jobs.data.total}
                limit={PAGE_SIZE}
                offset={offset}
                onOffset={setOffset}
              />
            ) : undefined
          }
        />
      </div>

      <Drawer
        open={selected !== null}
        title={selected ? `Job ${shortId(selected.id, 12)}` : ''}
        onClose={() => setSelected(null)}
      >
        {selected && <JobDetail job={selected} />}
      </Drawer>

      <Drawer open={showNew} title="New enrichment" onClose={() => setShowNew(false)}>
        <NewEnrichmentForm />
      </Drawer>
    </>
  )
}

function JobDetail({ job }: { job: JobOut }) {
  return (
    <>
      <KV
        pairs={[
          ['Status', <StatusBadge status={job.status} key="s" />],
          ['Pre-decision', job.pre_decision ?? '—'],
          ['Entity', `${job.entity_type} ${job.entity_id ?? ''}`],
          ['Requested fields', job.requested_fields.join(', ')],
          ['Estimated cost', fmtCredits(job.estimated_cost_credits)],
          ['Actual cost', fmtCredits(job.actual_cost_credits)],
          ['Dry run', job.dry_run ? 'yes' : 'no'],
          ['Trace ID', <span className="mono" key="t">{job.trace_id ?? '—'}</span>],
          ['Batch ID', job.batch_id ? <span className="mono">{job.batch_id}</span> : '—'],
          ['Created', fmtDate(job.created_at)],
          ['Finished', fmtDate(job.finished_at)],
        ]}
      />
      {job.error && <div className="error-note">{job.error}</div>}
      <h3 style={{ margin: '14px 0 6px', fontSize: 13 }}>Decision reasons</h3>
      {job.decision_reasons.length === 0 ? (
        <div className="faint">No decision reasons recorded.</div>
      ) : (
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {job.decision_reasons.map((r, i) => (
            <li key={i} className="muted">
              {typeof r === 'string' ? r : JSON.stringify(r)}
            </li>
          ))}
        </ul>
      )}
      <h3 style={{ margin: '14px 0 6px', fontSize: 13 }}>Result summary</h3>
      <JsonViewer data={job.result_summary} label="result_summary" defaultOpen />
      {job.entity_id && (
        <p>
          <Link to={`/entities/${job.entity_type}/${job.entity_id}`}>
            View entity {shortId(job.entity_id)} →
          </Link>
        </p>
      )}
    </>
  )
}

// ── New enrichment form ──────────────────────────────────────

const CONTACT_IDENTIFIERS: Array<{ key: string; label: string; type?: string }> = [
  { key: 'full_name', label: 'Full name' },
  { key: 'work_email', label: 'Work email', type: 'email' },
  { key: 'company_domain', label: 'Company domain' },
  { key: 'company_name', label: 'Company name' },
  { key: 'country', label: 'Country' },
  { key: 'external_crm_id', label: 'External CRM ID' },
]
const ACCOUNT_IDENTIFIERS: Array<{ key: string; label: string; type?: string }> = [
  { key: 'name', label: 'Company name' },
  { key: 'website', label: 'Website', type: 'url' },
  { key: 'root_domain', label: 'Root domain' },
  { key: 'external_crm_id', label: 'External CRM ID' },
]

function NewEnrichmentForm() {
  const queryClient = useQueryClient()
  const [entityType, setEntityType] = useState<'contact' | 'account'>('contact')
  const [identifiers, setIdentifiers] = useState<Record<string, string>>({})
  const [fields, setFields] = useState<string[]>([])
  const [campaignId, setCampaignId] = useState('')
  const [mode, setMode] = useState<'sync' | 'async'>('sync')
  const [dryRun, setDryRun] = useState(false)

  const campaigns = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => get<CampaignOut[]>('/v1/admin/campaigns'),
  })

  const submit = useMutation({
    mutationFn: () => {
      const entity: Record<string, string> = {}
      for (const [k, v] of Object.entries(identifiers)) {
        if (v.trim()) entity[k] = v.trim()
      }
      return post<JobOut>('/v1/enrichment/execute', {
        entity_type: entityType,
        entity,
        requested_fields: fields,
        campaign_id: campaignId || null,
        mode,
        dry_run: dryRun,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
  })

  const availableFields = entityType === 'contact' ? CONTACT_FIELDS : ACCOUNT_FIELDS
  const identifierDefs = entityType === 'contact' ? CONTACT_IDENTIFIERS : ACCOUNT_IDENTIFIERS
  const hasIdentifier = Object.values(identifiers).some((v) => v.trim())

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        submit.mutate()
      }}
    >
      <div className="field">
        <label htmlFor="enrich-entity-type">Entity type</label>
        <select
          id="enrich-entity-type"
          value={entityType}
          onChange={(e) => {
            setEntityType(e.target.value as 'contact' | 'account')
            setIdentifiers({})
            setFields([])
          }}
        >
          <option value="contact">contact</option>
          <option value="account">account</option>
        </select>
      </div>

      {identifierDefs.map((f) => (
        <div className="field" key={f.key}>
          <label htmlFor={`enrich-${f.key}`}>{f.label}</label>
          <input
            id={`enrich-${f.key}`}
            type={f.type ?? 'text'}
            value={identifiers[f.key] ?? ''}
            onChange={(e) => setIdentifiers((prev) => ({ ...prev, [f.key]: e.target.value }))}
          />
        </div>
      ))}

      <div className="field">
        <label id="enrich-fields-label">Requested fields</label>
        <div className="checkbox-grid" role="group" aria-labelledby="enrich-fields-label">
          {availableFields.map((f) => (
            <label className="checkbox-row" key={f} style={{ textTransform: 'none', letterSpacing: 0 }}>
              <input
                type="checkbox"
                checked={fields.includes(f)}
                onChange={(e) =>
                  setFields((prev) => (e.target.checked ? [...prev, f] : prev.filter((x) => x !== f)))
                }
              />
              {f}
            </label>
          ))}
        </div>
      </div>

      <div className="field">
        <label htmlFor="enrich-campaign">Campaign</label>
        <select
          id="enrich-campaign"
          value={campaignId}
          onChange={(e) => setCampaignId(e.target.value)}
        >
          <option value="">No campaign</option>
          {(campaigns.data ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </div>

      <div className="form-row">
        <div className="field">
          <label htmlFor="enrich-mode">Mode</label>
          <select id="enrich-mode" value={mode} onChange={(e) => setMode(e.target.value as 'sync' | 'async')}>
            <option value="sync">sync — wait for result</option>
            <option value="async">async — queue for worker</option>
          </select>
        </div>
        <div className="field" style={{ justifyContent: 'flex-end' }}>
          <label className="checkbox-row" style={{ textTransform: 'none', letterSpacing: 0 }}>
            <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
            Dry run (no spend, no writes)
          </label>
        </div>
      </div>

      {submit.isError && <ErrorNote error={submit.error} />}

      <button
        type="submit"
        className="btn primary"
        disabled={submit.isPending || fields.length === 0 || !hasIdentifier}
      >
        {submit.isPending ? 'Running…' : 'Execute enrichment'}
      </button>
      {(fields.length === 0 || !hasIdentifier) && (
        <div className="field-help" style={{ marginTop: 6 }}>
          Provide at least one identifier and one requested field.
        </div>
      )}

      {submit.data && (
        <div style={{ marginTop: 16 }}>
          <div className="info-note">
            Job <span className="mono">{shortId(submit.data.id, 12)}</span> finished with status{' '}
            <StatusBadge status={submit.data.status} />
          </div>
          <JobDetail job={submit.data} />
        </div>
      )}
    </form>
  )
}
