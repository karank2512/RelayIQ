import { PageHeader } from '../components/PageHeader'
import { Badge } from '../components/Badge'
import { KV } from '../components/misc'
import { API_BASE } from '../lib/api'
import { useAuth } from '../lib/auth'

const CAPABILITIES: Array<{ area: string; analyst: boolean; reviewer: boolean; operator: boolean; admin: boolean }> = [
  { area: 'View dashboards, entities, lineage', analyst: true, reviewer: true, operator: true, admin: true },
  { area: 'Act on review tasks / reverse approvals', analyst: false, reviewer: true, operator: true, admin: true },
  { area: 'Submit enrichments, manage campaigns & policies', analyst: false, reviewer: false, operator: true, admin: true },
  { area: 'View audit log', analyst: false, reviewer: false, operator: true, admin: true },
  { area: 'Provider settings & budgets', analyst: false, reviewer: false, operator: false, admin: true },
]

function Cell({ ok }: { ok: boolean }) {
  return <td className={ok ? 'cap-yes' : 'cap-no'}>{ok ? '✓' : '—'}</td>
}

export function SettingsPage() {
  const { session } = useAuth()
  return (
    <>
      <PageHeader
        title="Settings"
        subtitle="Session, environment, and role capabilities. Roles are verified server-side against the database on every request — never from request headers."
      />
      <section className="panel">
        <h3>Session</h3>
        <KV
          pairs={[
            ['Signed in as', session?.email],
            ['Role', session ? <Badge key="r" tone="accent">{session.role}</Badge> : '—'],
            ['Tenant', session?.tenant_id],
            ['API base', <code key="a">{API_BASE}</code>],
            ['API docs', <a key="d" href={`${API_BASE}/docs`} target="_blank" rel="noreferrer">{API_BASE}/docs</a>],
            ['Prometheus metrics', <a key="m" href={`${API_BASE}/metrics`} target="_blank" rel="noreferrer">{API_BASE}/metrics</a>],
          ]}
        />
      </section>
      <section className="panel">
        <h3>Role capabilities</h3>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr><th>Capability</th><th>Analyst</th><th>Reviewer</th><th>Operator</th><th>Admin</th></tr>
            </thead>
            <tbody>
              {CAPABILITIES.map((c) => (
                <tr key={c.area}>
                  <td>{c.area}</td>
                  <Cell ok={c.analyst} /><Cell ok={c.reviewer} /><Cell ok={c.operator} /><Cell ok={c.admin} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel">
        <h3>About this environment</h3>
        <p className="faint">
          Providers are deterministic simulators reading a synthetic world (all domains use the
          reserved <code>.test</code> TLD; every person and company is invented). Credit costs are
          synthetic economics. Live Clay and HubSpot integrations are implemented behind adapters
          but have not been verified against live accounts.
        </p>
      </section>
    </>
  )
}
