import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { get } from '../lib/api'
import type { CrmFieldChange, CrmSimRecordRow, CrmSyncAttemptRow, Page } from '../lib/types'
import { PageHeader } from '../components/PageHeader'
import { DataTable, Pagination } from '../components/DataTable'
import type { Column } from '../components/DataTable'
import { Badge, StatusBadge } from '../components/Badge'
import { Tabs } from '../components/Tabs'
import { JsonViewer } from '../components/JsonViewer'
import { errorMessage } from '../components/misc'
import { fmtAge, shortId } from '../lib/format'

const PAGE_SIZE = 25

function gateTone(gate: string): 'ok' | 'warn' | 'danger' | 'neutral' {
  switch (gate) {
    case 'write': return 'ok'
    case 'secondary_property': return 'accent' as never
    case 'require_approval':
    case 'mark_refresh': return 'warn'
    case 'no_write':
    case 'preserve_crm': return 'neutral'
    default: return 'neutral'
  }
}

function FieldChanges({ changes }: { changes: Record<string, CrmFieldChange> | null }) {
  if (!changes || !Object.keys(changes).length) return <span className="faint">No field decisions.</span>
  return (
    <table className="mini-table">
      <thead>
        <tr><th>Field</th><th>Before (CRM)</th><th>After</th><th>Gate</th><th>Reasons</th></tr>
      </thead>
      <tbody>
        {Object.entries(changes).map(([field, ch]) => (
          <tr key={field}>
            <td><code>{field}</code></td>
            <td>{ch.before != null ? String(ch.before) : <span className="faint">empty</span>}</td>
            <td>{ch.after != null ? String(ch.after) : '—'}</td>
            <td><Badge tone={gateTone(ch.gate ?? 'unknown')}>{ch.gate ?? 'unknown'}</Badge></td>
            <td className="faint">{(ch.reasons ?? []).join('; ')}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function AttemptsTab() {
  const [offset, setOffset] = useState(0)
  const q = useQuery({
    queryKey: ['crm', 'attempts', offset],
    queryFn: () => get<Page<CrmSyncAttemptRow>>('/v1/crm/sync-attempts', { limit: PAGE_SIZE, offset }),
  })
  const cols: Column<CrmSyncAttemptRow>[] = [
    { key: 'entity', header: 'Entity', render: (r) => <><Badge tone="neutral">{r.entity_type}</Badge> {shortId(r.entity_id)}</> },
    { key: 'status', header: 'Status', render: (r) => <StatusBadge status={r.status} /> },
    { key: 'dry', header: '', width: '70px', render: (r) => (r.dry_run ? <Badge tone="warn">dry run</Badge> : null) },
    { key: 'external', header: 'External ID', render: (r) => r.external_id ?? '—' },
    { key: 'fields', header: 'Fields', render: (r) => Object.keys(r.field_changes ?? {}).length },
    { key: 'error', header: 'Error', render: (r) => r.error ?? '—' },
    { key: 'at', header: 'When', render: (r) => fmtAge(r.created_at), sortValue: (r) => r.created_at ?? '' },
  ]
  return (
    <>
      <DataTable<CrmSyncAttemptRow>
        columns={cols}
        rows={q.data?.items}
        rowKey={(r) => r.id}
        loading={q.isPending}
        error={q.isError ? errorMessage(q.error) : null}
        emptyText="No sync attempts yet — accepted enrichments create them."
        renderExpanded={(r) => (
          <div className="expanded-detail">
            <FieldChanges changes={r.field_changes} />
            <JsonViewer data={r.gate_summary} label="gate summary" />
          </div>
        )}
      />
      <Pagination total={q.data?.total ?? 0} limit={PAGE_SIZE} offset={offset} onOffset={setOffset} />
    </>
  )
}

function SimulatorTab() {
  const [offset, setOffset] = useState(0)
  const q = useQuery({
    queryKey: ['crm', 'sim', offset],
    queryFn: () => get<Page<CrmSimRecordRow>>('/v1/crm/simulator/records', { limit: PAGE_SIZE, offset }),
  })
  const cols: Column<CrmSimRecordRow>[] = [
    { key: 'type', header: 'Object', render: (r) => <Badge tone="neutral">{r.object_type}</Badge> },
    { key: 'ext', header: 'External ID', render: (r) => <code>{r.external_id}</code> },
    { key: 'props', header: 'Properties', render: (r) => Object.keys(r.properties ?? {}).length },
    { key: 'updated', header: 'Updated', render: (r) => fmtAge(r.updated_at), sortValue: (r) => r.updated_at ?? '' },
  ]
  return (
    <>
      <p className="faint">
        This is what “the CRM” currently contains (simulator mode). Reviewers can verify a
        sync landed — and that gated fields did NOT land — without leaving RelayIQ.
      </p>
      <DataTable<CrmSimRecordRow>
        columns={cols}
        rows={q.data?.items}
        rowKey={(r) => r.id}
        loading={q.isPending}
        error={q.isError ? errorMessage(q.error) : null}
        emptyText="The CRM simulator is empty — sync an accepted record."
        renderExpanded={(r) => <JsonViewer data={r.properties} label="properties" defaultOpen />}
      />
      <Pagination total={q.data?.total ?? 0} limit={PAGE_SIZE} offset={offset} onOffset={setOffset} />
    </>
  )
}

export function CrmPage() {
  const [tab, setTab] = useState<'attempts' | 'simulator'>('attempts')
  return (
    <>
      <PageHeader
        title="CRM synchronization"
        subtitle="Every field passes the sync gate before writing: confidence, conflicts, staleness, reviewer decisions, and the existing CRM value all get a vote. Expand a row to see per-field before/after and the gate's reasons."
      />
      <Tabs
        tabs={[{ id: 'attempts', label: 'Sync attempts' }, { id: 'simulator', label: 'CRM simulator contents' }]}
        active={tab}
        onChange={setTab}
      />
      {tab === 'attempts' ? <AttemptsTab /> : <SimulatorTab />}
    </>
  )
}
