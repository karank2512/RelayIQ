import { statusTone } from '../lib/format'

export type BadgeTone = 'ok' | 'warn' | 'danger' | 'accent' | 'neutral'

interface BadgeProps {
  children: React.ReactNode
  /** Explicit tone; when omitted, `status` is mapped to a tone automatically. */
  tone?: BadgeTone
  status?: string | null
  title?: string
}

export function Badge({ children, tone, status, title }: BadgeProps) {
  const resolved = tone ?? statusTone(status ?? (typeof children === 'string' ? children : null))
  return (
    <span className={`badge ${resolved}`} title={title}>
      {children}
    </span>
  )
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="faint">—</span>
  return <Badge status={status}>{status}</Badge>
}
