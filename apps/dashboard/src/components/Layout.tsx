import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { Badge } from './Badge'
import { useAuth } from '../lib/auth'

const NAV_SECTIONS: Array<{ label: string; items: Array<{ to: string; label: string }> }> = [
  {
    label: 'Operate',
    items: [
      { to: '/', label: 'Overview' },
      { to: '/requests', label: 'Requests' },
      { to: '/entities', label: 'Entities' },
      { to: '/crm', label: 'CRM Sync' },
    ],
  },
  {
    label: 'Quality',
    items: [
      { to: '/review', label: 'Review Queue' },
      { to: '/analytics', label: 'Analytics' },
    ],
  },
  {
    label: 'Configure',
    items: [
      { to: '/providers', label: 'Providers' },
      { to: '/policies', label: 'Policies' },
      { to: '/campaigns', label: 'Campaigns' },
    ],
  },
  {
    label: 'System',
    items: [
      { to: '/audit', label: 'Audit Log' },
      { to: '/settings', label: 'Settings' },
    ],
  },
]

function roleTone(role: string): 'ok' | 'warn' | 'accent' | 'neutral' {
  switch (role) {
    case 'admin':
      return 'ok'
    case 'operator':
      return 'accent'
    case 'reviewer':
      return 'warn'
    default:
      return 'neutral'
  }
}

export function Layout() {
  const { session, logout } = useAuth()
  const navigate = useNavigate()

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="brand-mark">Relay</span>IQ
          <div className="faint" style={{ fontSize: 10.5, fontWeight: 500 }}>
            enrichment control plane
          </div>
        </div>
        <nav aria-label="Main navigation">
          {NAV_SECTIONS.map((section) => (
            <div className="nav-section" key={section.label}>
              <div className="nav-section-label">{section.label}</div>
              {section.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === '/'}
                  className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
                >
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <div className="main-col">
        <div className="topbar">
          <span className="user-email">{session?.email}</span>
          {session && <Badge tone={roleTone(session.role)}>{session.role}</Badge>}
          <button
            type="button"
            className="btn small"
            onClick={() => {
              logout()
              navigate('/login')
            }}
          >
            Log out
          </button>
        </div>
        <main className="page">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
