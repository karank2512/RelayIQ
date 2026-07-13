import { ApiError } from '../lib/api'

export function ErrorNote({ error }: { error: unknown }) {
  const message =
    error instanceof ApiError
      ? error.message
      : error instanceof Error
        ? error.message
        : 'Something went wrong.'
  return (
    <div className="error-note" role="alert">
      {message}
    </div>
  )
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  return 'Something went wrong.'
}

export function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403
}

/** Simple definition-list key/value block used across detail views. */
export function KV({ pairs }: { pairs: Array<[string, React.ReactNode]> }) {
  return (
    <dl className="kv-grid">
      {pairs.map(([k, v]) => (
        <FragmentKV key={k} k={k} v={v} />
      ))}
    </dl>
  )
}

function FragmentKV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <>
      <dt>{k}</dt>
      <dd>{v ?? <span className="faint">—</span>}</dd>
    </>
  )
}
