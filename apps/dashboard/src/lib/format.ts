/** Formatting helpers — every metric renders "—" when the API returns null. */

export function fmtNum(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function fmtCredits(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${fmtNum(v, digits)} cr`
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${(v * 100).toFixed(digits)}%`
}

export function fmtMs(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${Math.round(v)} ms`
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    year: '2-digit',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function fmtAge(iso: string | null | undefined): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  let secs = Math.max(0, (Date.now() - then) / 1000)
  const units: Array<[string, number]> = [
    ['d', 86400],
    ['h', 3600],
    ['m', 60],
  ]
  for (const [label, size] of units) {
    if (secs >= size) return `${Math.floor(secs / size)}${label} ago`
  }
  secs = Math.floor(secs)
  return `${secs}s ago`
}

export function shortId(id: string | null | undefined, len = 8): string {
  if (!id) return '—'
  return id.length > len ? `${id.slice(0, len)}…` : id
}

/** Map an arbitrary status string from the API onto a badge tone. */
export function statusTone(
  status: string | null | undefined,
): 'ok' | 'warn' | 'danger' | 'accent' | 'neutral' {
  if (!status) return 'neutral'
  const s = status.toLowerCase()
  if (
    ['completed', 'completed_cached', 'succeeded', 'success', 'accepted', 'active', 'fresh',
      'ok', 'closed', 'verified', 'auto_accept', 'accept'].some((k) => s === k || s.startsWith(k))
  )
    return 'ok'
  if (
    ['failed', 'rejected', 'error', 'expired', 'blocked_budget', 'blocked_policy', 'open',
      'perm_fail'].some((k) => s === k || s.startsWith(k))
  )
    return 'danger'
  if (
    ['awaiting_review', 'pending', 'partial', 'stale', 'aging', 'deferred', 'degraded',
      'half_open', 'require_review', 'warning', 'overridden', 'skipped'].some(
      (k) => s === k || s.startsWith(k),
    )
  )
    return 'warn'
  if (['running', 'queued', 'received', 'in_progress', 'dry_run'].some((k) => s === k)) return 'accent'
  return 'neutral'
}
