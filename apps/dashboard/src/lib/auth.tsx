import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { clearSession, loadSession, saveSession } from './api'
import type { AuthSession } from './api'

export type Role = 'analyst' | 'reviewer' | 'operator' | 'admin'

const ROLE_RANK: Record<string, number> = { analyst: 0, reviewer: 1, operator: 2, admin: 3 }

interface AuthContextValue {
  session: AuthSession | null
  login: (session: AuthSession) => void
  logout: () => void
  /** True when the current role is at least `role` in the analyst<reviewer<operator<admin ladder. */
  hasRole: (role: Role) => boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(() => loadSession())

  const login = useCallback((s: AuthSession) => {
    saveSession(s)
    setSession(s)
  }, [])

  const logout = useCallback(() => {
    clearSession()
    setSession(null)
  }, [])

  const hasRole = useCallback(
    (role: Role) => {
      if (!session) return false
      return (ROLE_RANK[session.role] ?? -1) >= ROLE_RANK[role]
    },
    [session],
  )

  const value = useMemo(
    () => ({ session, login, logout, hasRole }),
    [session, login, logout, hasRole],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { session } = useAuth()
  const location = useLocation()
  if (!session) return <Navigate to="/login" state={{ from: location.pathname }} replace />
  return <>{children}</>
}
