interface StatCardProps {
  label: string
  value: React.ReactNode
  /** Plain-English explanation (e.g. the metric formula), shown in a hover/focus tooltip. */
  hint?: string
  sub?: React.ReactNode
}

export function StatCard({ label, value, hint, sub }: StatCardProps) {
  return (
    <div className="stat-card">
      <div className="stat-label">
        <span>{label}</span>
        {hint && (
          <span className="hint-dot" tabIndex={0} role="note" aria-label={hint}>
            i<span className="hint-pop">{hint}</span>
          </span>
        )}
      </div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}
