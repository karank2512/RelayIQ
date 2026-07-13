/** Minimal typed API client. Attaches the bearer token and normalizes errors. */

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) || 'http://localhost:8000'

const AUTH_KEY = 'relayiq.auth'

export interface AuthSession {
  access_token: string
  role: string
  tenant_id: string
  user_id: string
  email: string
}

export function loadSession(): AuthSession | null {
  try {
    const raw = localStorage.getItem(AUTH_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as AuthSession
    if (!parsed.access_token) return null
    return parsed
  } catch {
    return null
  }
}

export function saveSession(session: AuthSession): void {
  localStorage.setItem(AUTH_KEY, JSON.stringify(session))
}

export function clearSession(): void {
  localStorage.removeItem(AUTH_KEY)
}

export class ApiError extends Error {
  status: number
  body: unknown

  constructor(status: number, message: string, body: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

function extractMessage(body: unknown, fallback: string): string {
  if (body && typeof body === 'object') {
    const b = body as Record<string, unknown>
    if (typeof b.detail === 'string') return b.detail
    if (Array.isArray(b.detail) && b.detail.length > 0) {
      const first = b.detail[0] as Record<string, unknown>
      if (typeof first?.msg === 'string') return first.msg
    }
    const err = b.error as Record<string, unknown> | undefined
    if (err && typeof err.message === 'string') return err.message
  }
  return fallback
}

interface RequestOptions {
  method?: string
  body?: unknown
  params?: Record<string, string | number | boolean | null | undefined>
  headers?: Record<string, string>
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const url = new URL(path.startsWith('http') ? path : API_BASE + path, window.location.origin)
  if (options.params) {
    for (const [k, v] of Object.entries(options.params)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
    }
  }
  const session = loadSession()
  const headers: Record<string, string> = { ...options.headers }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  if (session) headers['Authorization'] = `Bearer ${session.access_token}`

  let res: Response
  try {
    res = await fetch(url.toString(), {
      method: options.method ?? 'GET',
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    })
  } catch {
    throw new ApiError(0, `Cannot reach the API at ${API_BASE}. Is the backend running?`, null)
  }

  if (res.status === 401 && !path.includes('/auth/login')) {
    clearSession()
    if (!window.location.pathname.startsWith('/login')) {
      window.location.href = '/login'
    }
    throw new ApiError(401, 'Session expired — signing you out.', null)
  }

  const text = await res.text()
  let body: unknown = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = text
    }
  }

  if (!res.ok) {
    throw new ApiError(res.status, extractMessage(body, `Request failed (${res.status})`), body)
  }
  return body as T
}

export const get = <T>(path: string, params?: RequestOptions['params']) =>
  api<T>(path, { params })
export const post = <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
  api<T>(path, { method: 'POST', body, headers })
export const patch = <T>(path: string, body?: unknown) => api<T>(path, { method: 'PATCH', body })
export const put = <T>(path: string, body?: unknown) => api<T>(path, { method: 'PUT', body })
