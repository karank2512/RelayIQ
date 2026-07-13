import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { get } from '../lib/api'
import type { AuditEventRow, Page } from '../lib/types'
import { PageHeader } from '../components/PageHeader'
import { DataTable, Pagination } from '../components/DataTable'
import type { Column } from '../components/DataTable'
import { Badge } from '../components/Badge'
import { JsonViewer } from '../components/JsonViewer'
import { errorMessage, isForbidden } from '../components/misc'
import { fmtAge, shortId } from '../lib/format'

const PAGE_SIZE = 50

export function AuditPage() {
  const [offset, setOffset] = useState(0)
  const [action, setAction] = useState('')
  const q = useQuery({
    queryKey: ['audit', action, offset],
    queryFn: () => get<Page<AuditEventRow>>('/v1/audit', { action, limit: PAGE_SIZE, offset }),
  })

  if (q.isError && isForbidden(q.error)) {
    return (
      <>
        <PageHeader title="Audit log" />
        <div className="panel empty-state">
          <p>The audit log requires the <Badge tone="accent">operator</Badge> role or higher.</p>
          <p className="faint">Sign in as operator@demo.relayiq.test to view it in the demo environment.</p>
        </div>
      </>
    )
  }

  const cols: Column<AuditEventRow>[] = [
    { key: 'action', header: 'Action', render: (r) => <code>{r.action}</code>, sortValue: (r) => r.action },
    { key: 'object', header: 'Object', render: (r) => <>{r.object_type} {shortId(r.object_id)}</> },
    {
      key: 'actor', header: 'Actor',
      render: (r) => (
        <>
          <Badge tone={r.actor_type === 'user' ? 'accent' : 'neutral'}>{r.actor_type ?? 'system'}</Badge>{' '}
          {shortId(r.actor_user_id)}
        </>
      ),
    },
    { key: 'trace', header: 'Trace', render: (r) => shortId(r.trace_id) },
    { key: 'at', header: 'When', render: (r) => fmtAge(r.created_at), sortValue: (r) => r.created_at ?? '' },
  ]

  return (
    <>
      <PageHeader
        title="Audit log"
        subtitle="Append-only record of every state-changing action: reviews, reversals, config changes, CRM syncs. Expand a row for the before/after snapshot."
      />
      <div className="toolbar">
        <input
          type="search"
          className="input"
          placeholder="Filter by action (e.g. review.reverse, crm.sync)…"
          aria-label="Filter audit log by action"
          value={action}
          onChange={(e) => { setAction(e.target.value); setOffset(0) }}
        />
      </div>
      <DataTable<AuditEventRow>
        columns={cols}
        rows={q.data?.items}
        rowKey={(r) => r.id}
        loading={q.isPending}
        error={q.isError ? errorMessage(q.error) : null}
        emptyText="No audit events match."
        renderExpanded={(r) => (
          <div className="expanded-detail">
            <JsonViewer data={r.before} label="before" />
            <JsonViewer data={r.after} label="after" defaultOpen />
          </div>
        )}
      />
      <Pagination total={q.data?.total ?? 0} limit={PAGE_SIZE} offset={offset} onOffset={setOffset} />
    </>
  )
}
