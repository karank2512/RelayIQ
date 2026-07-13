import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { get } from '../lib/api'
import type { Page, ReviewTaskOut } from '../lib/types'
import { PageHeader } from '../components/PageHeader'
import { DataTable, Pagination } from '../components/DataTable'
import type { Column } from '../components/DataTable'
import { Badge, StatusBadge } from '../components/Badge'
import { ConfidenceBar } from '../components/ConfidenceBar'
import { Tabs } from '../components/Tabs'
import { errorMessage } from '../components/misc'
import { fmtAge, fmtPct } from '../lib/format'

const PAGE_SIZE = 25

interface ReviewMetrics {
  pending: number
  deferred: number
  resolved: number
  acceptance_rate: number | null
  override_rate: number | null
  reversal_rate: number | null
  avg_review_seconds: number | null
}

export function ReviewPage() {
  const [status, setStatus] = useState<'pending' | 'deferred' | 'all'>('pending')
  const [offset, setOffset] = useState(0)
  const navigate = useNavigate()

  const queue = useQuery({
    queryKey: ['review', 'queue', status, offset],
    queryFn: () => get<Page<ReviewTaskOut>>('/v1/review/queue', { status, limit: PAGE_SIZE, offset }),
  })
  const metrics = useQuery({
    queryKey: ['review', 'metrics'],
    queryFn: () => get<ReviewMetrics>('/v1/review/metrics'),
  })

  const cols: Column<ReviewTaskOut>[] = [
    { key: 'priority', header: 'Prio', width: '58px', align: 'right', render: (r) => r.priority, sortValue: (r) => r.priority },
    { key: 'entity', header: 'Entity', render: (r) => <Badge tone="neutral">{r.entity_type}</Badge> },
    { key: 'field', header: 'Field', render: (r) => (r.field_name ? <code>{r.field_name}</code> : <em>record-level</em>) },
    { key: 'reason', header: 'Reason', render: (r) => <span className="truncate" title={r.reason}>{r.reason}</span> },
    {
      key: 'confidence', header: 'Confidence',
      render: (r) => (r.confidence != null ? <ConfidenceBar value={r.confidence} /> : <span className="faint">—</span>),
      sortValue: (r) => r.confidence,
    },
    { key: 'suggested', header: 'Suggested', render: (r) => r.suggested_value ?? <span className="faint">—</span> },
    { key: 'status', header: 'Status', render: (r) => <StatusBadge status={r.status} /> },
    { key: 'age', header: 'Age', render: (r) => fmtAge(r.created_at), sortValue: (r) => r.created_at ?? '' },
  ]

  const m = metrics.data
  return (
    <>
      <PageHeader
        title="Review queue"
        subtitle="Conflicting or low-confidence values wait here — nothing below the confidence bar reaches the CRM without a human decision."
      />
      {m && (
        <div className="stat-strip" role="status">
          <span><strong>{m.pending}</strong> pending</span>
          <span><strong>{m.deferred}</strong> deferred</span>
          <span>acceptance {fmtPct(m.acceptance_rate)}</span>
          <span>override {fmtPct(m.override_rate)}</span>
          <span>reversal {fmtPct(m.reversal_rate)}</span>
          {m.avg_review_seconds != null && <span>avg review {Math.round(m.avg_review_seconds)}s</span>}
        </div>
      )}
      <Tabs
        tabs={[
          { id: 'pending', label: 'Pending' },
          { id: 'deferred', label: 'Deferred' },
          { id: 'all', label: 'All' },
        ]}
        active={status}
        onChange={(t) => { setStatus(t); setOffset(0) }}
      />
      <DataTable<ReviewTaskOut>
        columns={cols}
        rows={queue.data?.items}
        rowKey={(r) => r.id}
        loading={queue.isPending}
        error={queue.isError ? errorMessage(queue.error) : null}
        emptyText="Queue is clear — conflicting enrichments will land here."
        onRowClick={(r) => navigate(`/review/${r.id}`)}
      />
      <Pagination total={queue.data?.total ?? 0} limit={PAGE_SIZE} offset={offset} onOffset={setOffset} />
    </>
  )
}
