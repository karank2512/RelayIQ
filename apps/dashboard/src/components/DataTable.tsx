import { useMemo, useState } from 'react'

export interface Column<T> {
  key: string
  header: React.ReactNode
  render: (row: T) => React.ReactNode
  /** Provide to make the column sortable. */
  sortValue?: (row: T) => string | number | null
  width?: string
  align?: 'left' | 'right'
}

interface DataTableProps<T> {
  columns: Column<T>[]
  rows: T[] | undefined
  rowKey: (row: T) => string
  loading?: boolean
  error?: string | null
  emptyText?: string
  onRowClick?: (row: T) => void
  /** Rendered under a clicked/expanded row. */
  renderExpanded?: (row: T) => React.ReactNode
  footer?: React.ReactNode
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  loading,
  error,
  emptyText = 'Nothing here yet.',
  onRowClick,
  renderExpanded,
  footer,
}: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<1 | -1>(1)
  const [expandedKey, setExpandedKey] = useState<string | null>(null)

  const sorted = useMemo(() => {
    if (!rows) return []
    if (!sortKey) return rows
    const col = columns.find((c) => c.key === sortKey)
    if (!col?.sortValue) return rows
    const sv = col.sortValue
    return [...rows].sort((a, b) => {
      const va = sv(a)
      const vb = sv(b)
      if (va === null || va === undefined) return 1
      if (vb === null || vb === undefined) return -1
      if (va < vb) return -sortDir
      if (va > vb) return sortDir
      return 0
    })
  }, [rows, sortKey, sortDir, columns])

  const toggleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 1 ? -1 : 1))
    } else {
      setSortKey(key)
      setSortDir(1)
    }
  }

  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                style={c.width ? { width: c.width } : undefined}
                className={[
                  c.sortValue ? 'sortable' : '',
                  c.align === 'right' ? 'right' : '',
                ].join(' ')}
                onClick={c.sortValue ? () => toggleSort(c.key) : undefined}
                aria-sort={
                  sortKey === c.key ? (sortDir === 1 ? 'ascending' : 'descending') : undefined
                }
              >
                {c.header}
                {sortKey === c.key && (sortDir === 1 ? ' ▲' : ' ▼')}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading &&
            Array.from({ length: 5 }, (_, i) => (
              <tr key={`skeleton-${i}`}>
                {columns.map((c) => (
                  <td key={c.key}>
                    <div className="skeleton" style={{ width: `${45 + ((i * 17) % 40)}%` }} />
                  </td>
                ))}
              </tr>
            ))}
          {!loading && error && (
            <tr>
              <td colSpan={columns.length}>
                <div className="error-note" role="alert">
                  {error}
                </div>
              </td>
            </tr>
          )}
          {!loading && !error && sorted.length === 0 && (
            <tr>
              <td colSpan={columns.length}>
                <div className="table-empty">{emptyText}</div>
              </td>
            </tr>
          )}
          {!loading &&
            !error &&
            sorted.map((row) => {
              const key = rowKey(row)
              const clickable = Boolean(onRowClick || renderExpanded)
              return (
                <FragmentRow
                  key={key}
                  row={row}
                  columns={columns}
                  clickable={clickable}
                  expanded={expandedKey === key}
                  onClick={() => {
                    if (renderExpanded) setExpandedKey((k) => (k === key ? null : key))
                    onRowClick?.(row)
                  }}
                  renderExpanded={renderExpanded}
                />
              )
            })}
        </tbody>
      </table>
      {footer}
    </div>
  )
}

function FragmentRow<T>({
  row,
  columns,
  clickable,
  expanded,
  onClick,
  renderExpanded,
}: {
  row: T
  columns: Column<T>[]
  clickable: boolean
  expanded: boolean
  onClick: () => void
  renderExpanded?: (row: T) => React.ReactNode
}) {
  return (
    <>
      <tr className={clickable ? 'clickable' : ''} onClick={clickable ? onClick : undefined}>
        {columns.map((c) => (
          <td key={c.key} className={c.align === 'right' ? 'right' : ''}>
            {c.render(row)}
          </td>
        ))}
      </tr>
      {expanded && renderExpanded && (
        <tr>
          <td colSpan={columns.length} style={{ background: 'var(--bg)' }}>
            {renderExpanded(row)}
          </td>
        </tr>
      )}
    </>
  )
}

export function Pagination({
  total,
  limit,
  offset,
  onOffset,
}: {
  total: number
  limit: number
  offset: number
  onOffset: (offset: number) => void
}) {
  if (total <= limit) return null
  const page = Math.floor(offset / limit) + 1
  const pages = Math.ceil(total / limit)
  return (
    <div className="pagination">
      <span>
        Page {page} of {pages} ({total.toLocaleString()} rows)
      </span>
      <button
        type="button"
        className="btn small"
        disabled={offset === 0}
        onClick={() => onOffset(Math.max(0, offset - limit))}
      >
        Prev
      </button>
      <button
        type="button"
        className="btn small"
        disabled={offset + limit >= total}
        onClick={() => onOffset(offset + limit)}
      >
        Next
      </button>
    </div>
  )
}
