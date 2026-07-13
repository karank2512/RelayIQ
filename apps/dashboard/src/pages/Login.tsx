import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { post } from '../lib/api'
import type { AuthSession } from '../lib/api'
import { useAuth } from '../lib/auth'
import { errorMessage } from '../components/misc'

const DEMO_USERS = [
  'admin@demo.relayiq.test',
  'operator@demo.relayiq.test',
  'reviewer@demo.relayiq.test',
  'analyst@demo.relayiq.test',
]
const DEMO_PASSWORD = 'relayiq-demo-password'

export function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const session = await post<AuthSession>('/v1/auth/login', { email, password })
      login(session)
      navigate(from, { replace: true })
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-card">
        <h1>
          <span style={{ color: 'var(--accent)' }}>Relay</span>IQ
        </h1>
        <div className="login-sub">Sign in to the operations dashboard</div>
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor="login-email">Email</label>
            <input
              id="login-email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="login-password">Password</label>
            <input
              id="login-password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error && (
            <div className="error-note" role="alert">
              {error}
            </div>
          )}
          <button type="submit" className="btn primary" disabled={busy} style={{ width: '100%' }}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <div className="demo-hint">
          <div className="demo-title">
            Demo credentials <span className="badge warn">DEV ONLY</span>
          </div>
          <ul>
            {DEMO_USERS.map((u) => (
              <li key={u}>
                <button
                  type="button"
                  onClick={() => {
                    setEmail(u)
                    setPassword(DEMO_PASSWORD)
                  }}
                >
                  {u}
                </button>
              </li>
            ))}
          </ul>
          Password for all: <span className="mono">{DEMO_PASSWORD}</span>. Click a user to
          prefill.
        </div>
      </div>
    </div>
  )
}
