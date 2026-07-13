import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { get } from '../lib/api'
import type { CanonicalField, EntityDetail } from '../lib/types'
import { PageHeader } from '../components/PageHeader'
import { DataTable } from '../components/DataTable'
import type { Column } from '../components/DataTable'
import { Badge, StatusBadge } from '../components/Badge'
import { ConfidenceBar } from '../components/ConfidenceBar'
import { ErrorNote, KV } from '../components/misc'
import { fmtAge } from '../lib/format'

function stalenessTone(state: string | null): 'ok' | 'warn' | 'danger' | 'neutral' {
  switch (state) {
    case 'fresh': return 'ok'
    case 'aging': return 'warn'
    case 'stale':
    case 'expired': return 'danger'
    default: return 'neutral'
  }
}

const SUMMARY_KEYS: Record<string, string[]> = {
  contact: ['full_name', 'work_email', 'job_title', 'seniority', 'department', 'company_name', 'company_domain', 'country'],
  account: ['name', 'root_domain', 'website', 'industry', 'employee_count', 'employee_range', 'hq_city', 'hq_country', 'founded_year'],
}

export function EntityDetailPage() {
  const { entityType = 'contact', entityId = '' } = useParams()
  const query = useQuery({
    queryKey: ['entity', entityType, entityId],
    queryFn: () => get<EntityDetail>(`/v1/entities/${entityType}/${entityId}`),
  })

  const cols: Column<CanonicalField>[] = [
    { key: 'field', header: 'Field', render: (r) => <code>{r.field_name}</code>, sortValue: (r) => r.field_name },
    { key: 'value', header: 'Canonical value', render: (r) => r.value ?? <span className="faint">—</span> },
    {
      key: 'confidence', header: 'Confidence',
      render: (r) => (r.confidence != null ? <ConfidenceBar value={r.confidence} /> : <span className="faint">—</span>),
      sortValue: (r) => r.confidence,
    },
    {
      key: 'staleness', header: 'Staleness',
      render: (r) => <Badge tone={stalenessTone(r.staleness_state)}>{r.staleness_state ?? 'unknown'}</Badge>,
    },
    { key: 'source', header: 'Source', render: (r) => <Badge tone="neutral">{r.source_kind ?? 'provider'}</Badge> },
    { key: 'verified', header: 'Verified', render: (r) => fmtAge(r.last_verified_at) },
    {
      key: 'lineage', header: '', width: '90px',
      render: (r) => (
        <Link className="btn small" to={`/lineage/${entityType}/${entityId}/${r.field_name}`}>
          Lineage
        </Link>
      ),
    },
  ]

  const entity = query.data?.entity
  const summary = entity
    ? (SUMMARY_KEYS[entityType] ?? []).map(
        (k) => [k.replace(/_/g, ' '), entity[k] as React.ReactNode] as [string, React.ReactNode],
      )
    : []

  return (
    <>
      <PageHeader
        title={entity ? String(entity.full_name ?? entity.name ?? 'Entity') : 'Entity'}
        subtitle={`${entityType} · ${entityId}`}
        actions={entity?.record_status != null ? <StatusBadge status={String(entity.record_status)} /> : undefined}
      />
      {query.isError && <ErrorNote error={query.error} />}
      {entity && (
        <section className="panel">
          <KV pairs={summary} />
        </section>
      )}
      <h2 className="section-title">Canonical fields</h2>
      <p className="faint" style={{ marginTop: -6 }}>
        Each value was selected from preserved provider observations by the reconciliation
        engine. Open a field&apos;s lineage to see every observation, decision, and sync.
      </p>
      <DataTable<CanonicalField>
        columns={cols}
        rows={query.data?.canonical_fields}
        rowKey={(r) => r.field_name}
        loading={query.isPending}
        emptyText="No canonical fields yet — run an enrichment for this entity."
      />
    </>
  )
}
