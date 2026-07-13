import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { get } from '../lib/api'
import type { AccountRow, ContactRow, Page } from '../lib/types'
import { PageHeader } from '../components/PageHeader'
import { DataTable, Pagination } from '../components/DataTable'
import type { Column } from '../components/DataTable'
import { Tabs } from '../components/Tabs'
import { ConfidenceBar } from '../components/ConfidenceBar'
import { Badge } from '../components/Badge'
import { errorMessage } from '../components/misc'
import { fmtAge, fmtNum } from '../lib/format'

const PAGE_SIZE = 25

export function EntitiesPage() {
  const [tab, setTab] = useState<'accounts' | 'contacts'>('contacts')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const navigate = useNavigate()

  const query = useQuery({
    queryKey: ['entities', tab, q, offset],
    queryFn: () =>
      get<Page<AccountRow | ContactRow>>(`/v1/${tab}`, { q, limit: PAGE_SIZE, offset }),
  })

  const contactCols: Column<ContactRow>[] = [
    { key: 'name', header: 'Name', render: (r) => r.full_name ?? '—', sortValue: (r) => r.full_name ?? '' },
    { key: 'email', header: 'Work email', render: (r) => r.work_email ?? <span className="faint">missing</span> },
    { key: 'title', header: 'Job title', render: (r) => r.job_title ?? <span className="faint">not enriched</span> },
    { key: 'seniority', header: 'Seniority', render: (r) => (r.seniority ? <Badge tone="neutral">{r.seniority}</Badge> : '—') },
    { key: 'domain', header: 'Company domain', render: (r) => r.company_domain ?? '—' },
    {
      key: 'confidence', header: 'Confidence',
      render: (r) => (r.record_confidence != null ? <ConfidenceBar value={r.record_confidence} /> : <span className="faint">—</span>),
      sortValue: (r) => r.record_confidence,
    },
    { key: 'verified', header: 'Verified', render: (r) => fmtAge(r.last_verified_at), sortValue: (r) => r.last_verified_at ?? '' },
  ]

  const accountCols: Column<AccountRow>[] = [
    { key: 'name', header: 'Company', render: (r) => r.name ?? '—', sortValue: (r) => r.name ?? '' },
    { key: 'domain', header: 'Root domain', render: (r) => r.root_domain ?? '—' },
    { key: 'industry', header: 'Industry', render: (r) => r.industry ?? <span className="faint">not enriched</span> },
    { key: 'employees', header: 'Employees', align: 'right', render: (r) => (r.employee_count != null ? fmtNum(r.employee_count) : '—'), sortValue: (r) => r.employee_count },
    { key: 'range', header: 'Range', render: (r) => r.employee_range ?? '—' },
    { key: 'hq', header: 'HQ', render: (r) => [r.hq_city, r.hq_country].filter(Boolean).join(', ') || '—' },
    {
      key: 'confidence', header: 'Confidence',
      render: (r) => (r.record_confidence != null ? <ConfidenceBar value={r.record_confidence} /> : <span className="faint">—</span>),
      sortValue: (r) => r.record_confidence,
    },
  ]

  return (
    <>
      <PageHeader
        title="Entities"
        subtitle="Canonical accounts and contacts. Enriched values carry field-level confidence and staleness — open a record to inspect its canonical fields and lineage."
      />
      <div className="toolbar">
        <Tabs
          tabs={[{ id: 'contacts', label: 'Contacts' }, { id: 'accounts', label: 'Accounts' }]}
          active={tab}
          onChange={(t) => { setTab(t); setOffset(0) }}
        />
        <input
          type="search"
          className="input"
          placeholder={tab === 'contacts' ? 'Search name, email, or domain…' : 'Search name or domain…'}
          aria-label="Search entities"
          value={q}
          onChange={(e) => { setQ(e.target.value); setOffset(0) }}
        />
      </div>
      {tab === 'contacts' ? (
        <DataTable<ContactRow>
          columns={contactCols}
          rows={query.data?.items as ContactRow[] | undefined}
          rowKey={(r) => r.id}
          loading={query.isPending}
          error={query.isError ? errorMessage(query.error) : null}
          emptyText="No contacts match. Seed data with `make seed` or submit an enrichment."
          onRowClick={(r) => navigate(`/entities/contact/${r.id}`)}
        />
      ) : (
        <DataTable<AccountRow>
          columns={accountCols}
          rows={query.data?.items as AccountRow[] | undefined}
          rowKey={(r) => r.id}
          loading={query.isPending}
          error={query.isError ? errorMessage(query.error) : null}
          emptyText="No accounts match."
          onRowClick={(r) => navigate(`/entities/account/${r.id}`)}
        />
      )}
      <Pagination
        total={query.data?.total ?? 0}
        limit={PAGE_SIZE}
        offset={offset}
        onOffset={setOffset}
      />
    </>
  )
}
