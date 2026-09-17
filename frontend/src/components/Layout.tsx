import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { NotificationBell } from './NotificationBell'
import { SoundToggle } from './SoundToggle'

interface NavItem {
  label: string
  path: string
  implemented: boolean
  // Omitted = visible to every authenticated role (Dashboard, Audit Trail —
  // baseline situational awareness). Otherwise, only these roles see the
  // link at all. Mirrors each role's own description on the Administration
  // page — e.g. "IT/Audit Technical User: manages gateways and data
  // connections" is why that role gets Data Sources/Gateways but not Risks.
  roles?: string[]
}

interface NavGroup {
  label: string | null
  items: NavItem[]
}

const PLATFORM_ADMIN = ['Platform Super Admin', 'Platform Admin']
const AUDIT_TEAM = ['Platform Super Admin', 'Audit Manager', 'Auditor', 'Compliance Manager']
const TECHNICAL_TEAM = ['Platform Super Admin', 'IT/Audit Technical User']
const CLIENT_ADMIN = ['Client Organisation Admin', 'Client IT Admin']

const NAV: NavGroup[] = [
  { label: null, items: [{ label: 'Dashboard', path: '/', implemented: true }] },
  {
    label: 'Organization',
    items: [
      { label: 'Organizations', path: '/organizations', implemented: true, roles: PLATFORM_ADMIN },
      { label: 'Engagements', path: '/engagements', implemented: true, roles: PLATFORM_ADMIN },
      {
        label: 'Business Units',
        path: '/business-units',
        implemented: true,
        roles: [...PLATFORM_ADMIN, ...AUDIT_TEAM, ...CLIENT_ADMIN, 'Process Owner', 'Executive'],
      },
      {
        label: 'Users',
        path: '/users',
        implemented: true,
        roles: [...PLATFORM_ADMIN, ...CLIENT_ADMIN],
      },
    ],
  },
  {
    label: 'Audit Framework',
    items: [
      {
        label: 'Business Processes',
        path: '/business-processes',
        implemented: true,
        roles: [...AUDIT_TEAM, ...CLIENT_ADMIN, 'Process Owner', 'Executive', 'Read Only'],
      },
      {
        label: 'Risks',
        path: '/risks',
        implemented: true,
        roles: [...AUDIT_TEAM, ...CLIENT_ADMIN, 'Process Owner', 'Executive', 'Read Only'],
      },
      {
        label: 'Controls',
        path: '/controls',
        implemented: true,
        roles: [...AUDIT_TEAM, ...CLIENT_ADMIN, 'Control Owner', 'Executive', 'Read Only'],
      },
      {
        // Deliberately excludes CLIENT_ADMIN — neither Client Organisation
        // Admin nor Client IT Admin hold audit_framework:manage, and their
        // own role descriptions (org/user administration; data sources and
        // gateways) don't cover audit test oversight either, unlike Control
        // Owner/Read Only who have a real reason to view test status.
        label: 'Audit Tests',
        path: '/audit-tests',
        implemented: true,
        roles: [...AUDIT_TEAM, ...TECHNICAL_TEAM, 'Control Owner', 'Read Only'],
      },
      {
        // Same visibility as Audit Tests, for the same reason — viewing is
        // open to anyone with a real reason to see test behavior, but only
        // audit_framework:manage holders (AUDIT_TEAM/TECHNICAL_TEAM) get the
        // editable inputs; everyone else on this list sees read-only values.
        label: 'Rule Parameters',
        path: '/rule-parameters',
        implemented: true,
        roles: [...AUDIT_TEAM, ...TECHNICAL_TEAM, 'Control Owner', 'Read Only'],
      },
    ],
  },
  {
    label: 'Data',
    items: [
      { label: 'Data Sources', path: '/data-sources', implemented: true, roles: [...TECHNICAL_TEAM, ...CLIENT_ADMIN, 'Support User'] },
      { label: 'Gateways', path: '/gateways', implemented: true, roles: [...TECHNICAL_TEAM, ...CLIENT_ADMIN, 'Support User'] },
      { label: 'Devices', path: '/devices', implemented: true, roles: [...TECHNICAL_TEAM, ...CLIENT_ADMIN, 'Support User', 'Device Manager', 'Audit Manager', 'Platform Admin'] },
      { label: 'Connections', path: '/connections', implemented: true, roles: [...TECHNICAL_TEAM, ...CLIENT_ADMIN] },
      { label: 'Data Catalogue', path: '/data-catalogue', implemented: true, roles: [...TECHNICAL_TEAM, ...CLIENT_ADMIN] },
      { label: 'Connector Catalogue', path: '/connector-catalogue', implemented: true, roles: [...TECHNICAL_TEAM, ...CLIENT_ADMIN] },
    ],
  },
  {
    label: 'Continuous Monitoring',
    items: [
      { label: 'Monitoring', path: '/monitoring', implemented: true, roles: [...AUDIT_TEAM, ...CLIENT_ADMIN] },
      { label: 'Executions', path: '/executions', implemented: true, roles: [...AUDIT_TEAM, ...CLIENT_ADMIN] },
      {
        label: 'Exceptions',
        path: '/exceptions',
        implemented: true,
        roles: [...AUDIT_TEAM, ...CLIENT_ADMIN, 'Exception Owner', 'Control Owner', 'Executive', 'Read Only'],
      },
    ],
  },
  {
    label: 'Assurance',
    items: [
      {
        label: 'Findings',
        path: '/findings',
        implemented: true,
        roles: [...AUDIT_TEAM, ...CLIENT_ADMIN, 'Exception Owner', 'Reviewer', 'Executive', 'Read Only'],
      },
      { label: 'Remediation', path: '/remediation', implemented: true, roles: [...AUDIT_TEAM, ...CLIENT_ADMIN, 'Exception Owner'] },
      { label: 'Re-tests', path: '/retests', implemented: true, roles: [...AUDIT_TEAM, 'Reviewer'] },
    ],
  },
  {
    label: null,
    items: [
      { label: 'Evidence', path: '/evidence', implemented: true, roles: [...AUDIT_TEAM, 'Reviewer'] },
      {
        label: 'Audit Trail',
        path: '/audit-trail',
        implemented: true,
        // Must match the backend's audit_log:read permission holders.
        roles: ['Platform Super Admin', 'Platform Admin', 'Audit Manager', 'Client Organisation Admin', 'Support User'],
      },
      { label: 'Administration', path: '/administration', implemented: true, roles: PLATFORM_ADMIN },
    ],
  },
]

export function Layout() {
  const { user, logout, hasRole } = useAuth()
  const { organizationId } = useActiveOrganization()
  // Off-canvas on phones/small tablets (below md) — a fixed 256px sidebar
  // would otherwise eat most of a ~360-400px screen, leaving almost no
  // room for actual content. At md and above this is ignored entirely;
  // the sidebar is always visible, same as before.
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  const visibleGroups = NAV.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.roles || hasRole(...item.roles)),
  })).filter((group) => group.items.length > 0)

  const navContent = (
    <>
      <div className="border-b border-line px-5 py-4">
        <span className="text-lg font-semibold text-ink">Audit Platform</span>
        <p className="text-xs text-ink-soft">Continuous Assurance</p>
      </div>
      <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
        {visibleGroups.map((group, idx) => (
          <div key={idx}>
            {group.label && (
              <div className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-soft">
                {group.label}
              </div>
            )}
            <div className="space-y-0.5">
              {group.items.map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  onClick={() => setMobileNavOpen(false)}
                  className={({ isActive }) =>
                    `flex items-center justify-between rounded-md px-2 py-1.5 text-sm ${
                      isActive ? 'bg-accent-soft text-accent-ink font-medium' : 'text-ink hover:bg-bg'
                    }`
                  }
                >
                  <span>{item.label}</span>
                  {!item.implemented && (
                    <span className="rounded-full bg-bg px-1.5 py-0.5 text-[10px] text-ink-soft">soon</span>
                  )}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>
      <div className="border-t border-line px-4 py-3">
        <div className="text-sm font-medium text-ink">
          {user?.first_name} {user?.last_name}
        </div>
        <div className="text-xs text-ink-soft">{user?.roles.join(', ') || 'No role assigned'}</div>
        <div className="mt-2 flex items-center gap-3">
          <NavLink to="/profile" onClick={() => setMobileNavOpen(false)} className="text-xs font-medium text-ink-soft hover:text-ink hover:underline">
            Profile
          </NavLink>
          <button onClick={logout} className="text-xs font-medium text-accent-ink hover:underline">
            Log out
          </button>
        </div>
      </div>
    </>
  )

  return (
    <div className="flex h-screen overflow-x-hidden bg-bg">
      {mobileNavOpen && (
        <div className="fixed inset-0 z-30 bg-black/40 md:hidden" onClick={() => setMobileNavOpen(false)} />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-64 flex-shrink-0 flex-col border-r border-line bg-surface transition-transform duration-200 md:static md:translate-x-0 ${
          mobileNavOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {navContent}
      </aside>
      <main className="flex-1 overflow-y-auto">
        <div className="flex items-center justify-between gap-2 border-b border-line bg-surface px-4 py-2 sm:px-8">
          <button
            onClick={() => setMobileNavOpen(true)}
            aria-label="Open navigation menu"
            className="rounded-md border border-line p-1.5 text-ink-soft md:hidden"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </svg>
          </button>
          <div className="flex flex-1 items-center justify-end gap-2">
            <SoundToggle />
            <NotificationBell organizationId={organizationId} />
          </div>
        </div>
        {user && user.password_reminder_days_remaining != null && !user.must_change_password && (
          <div className="border-b border-orange-200 bg-orange-50 px-4 py-2 text-xs text-orange-800 sm:px-8">
            Your password expires in {user.password_reminder_days_remaining} day
            {user.password_reminder_days_remaining === 1 ? '' : 's'} —{' '}
            <NavLink to="/profile" className="font-medium underline">
              change it now
            </NavLink>
            .
          </div>
        )}
        <div className="p-4 sm:p-8">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
