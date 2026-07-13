interface ConfidenceBarProps {
  value: number | null | undefined
  /** thresholds: >= high → ok, >= mid → warn, else danger */
  high?: number
  mid?: number
}

export function ConfidenceBar({ value, high = 0.8, mid = 0.5 }: ConfidenceBarProps) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="faint">—</span>
  }
  const clamped = Math.max(0, Math.min(1, value))
  const color = clamped >= high ? 'var(--ok)' : clamped >= mid ? 'var(--warn)' : 'var(--danger)'
  return (
    <span
      className="confidence-bar"
      role="meter"
      aria-valuemin={0}
      aria-valuemax={1}
      aria-valuenow={clamped}
      aria-label={`confidence ${clamped.toFixed(2)}`}
    >
      <span className="track">
        <span className="fill" style={{ width: `${clamped * 100}%`, background: color }} />
      </span>
      <span className="num">{clamped.toFixed(2)}</span>
    </span>
  )
}
