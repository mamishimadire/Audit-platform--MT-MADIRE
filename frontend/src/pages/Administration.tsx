import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import type { PermissionOut, RoleOut, SodWorkflowOut } from '../types/api'

export function AdministrationPage() {
  const { hasRole } = useAuth()
  const isPlatformAdmin = hasRole('Platform Super Admin', 'Platform Admin')
  const [roles, setRoles] = useState<RoleOut[]>([])
  const [permissions, setPermissions] = useState<PermissionOut[]>([])
  const [workflows, setWorkflows] = useState<SodWorkflowOut[]>([])

  useEffect(() => {
    // The backend enforces this too (organizations:manage) — this just
    // avoids firing requests that would 403, and shows a clear message
    // instead of an empty-looking page for anyone reaching this route
    // directly (e.g. by URL) without platform-admin access.
    if (!isPlatformAdmin) return
    apiClient.get<RoleOut[]>('/reference/roles').then((res) => setRoles(res.data))
    apiClient.get<PermissionOut[]>('/reference/permissions').then((res) => setPermissions(res.data))
    apiClient.get<SodWorkflowOut[]>('/reference/sod-workflows').then((res) => setWorkflows(res.data))
  }, [isPlatformAdmin])

  if (!isPlatformAdmin) {
    return (
      <div>
        <h1 className="text-2xl font-semibold text-ink">Administration</h1>
        <p className="mt-4 rounded-lg border border-line bg-surface p-4 text-sm text-ink-soft">
          This page is only available to platform administrators.
        </p>
      </div>
    )
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Administration</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Roles and permissions are reference data seeded by the database migrations, not hard-coded in the frontend.
      </p>

      <div className="mt-6 rounded-lg border border-line bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">Internal vs. external — the organization boundary</h2>
        <p className="mt-1 text-xs text-ink-soft max-w-3xl">
          <strong>Internal (MT Audit)</strong> roles belong to our own staff. An internal user's account has no
          organization of its own — they must be explicitly granted access to each client they work with (see{' '}
          <em>user_organization_scope</em>), except <strong>Platform Super Admin</strong>, which bypasses per-client
          scoping entirely as a tightly controlled break-glass account. <strong>External (Client)</strong> roles
          belong to a specific client's own users — their account is permanently tied to that one organization and
          can never see another client or any internal system, enforced in the backend on every request, not just
          hidden in the UI. A Client Organisation Admin can only ever assign other External roles to their own
          users — assigning an Internal role from a client-side screen is rejected server-side
          (<em>app.services.user_service</em>), not merely hidden.
        </p>
        <div className="mt-4 grid gap-6 md:grid-cols-2">
          {(['MT_AUDIT_INTERNAL', 'CLIENT_EXTERNAL'] as const).map((classification) => (
            <div key={classification}>
              <div className="flex items-center gap-2">
                <span
                  className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                    classification === 'MT_AUDIT_INTERNAL' ? 'bg-accent-soft text-accent-ink' : 'bg-bg text-ink-soft'
                  }`}
                >
                  {classification === 'MT_AUDIT_INTERNAL' ? 'Internal — MT Audit' : 'External — Client'}
                </span>
                <span className="text-xs text-ink-faint">
                  {roles.filter((r) => r.account_classification === classification).length} roles
                </span>
              </div>
              <ul className="mt-3 space-y-3">
                {roles
                  .filter((role) => role.account_classification === classification)
                  .map((role) => (
                    <li key={role.role_id} className="rounded-md border border-line p-3">
                      <div className="text-sm font-medium text-ink">{role.role_name}</div>
                      <div className="mt-0.5 text-xs text-ink-soft">{role.description}</div>
                      <div className="mt-2 flex flex-wrap gap-1">
                        {role.permissions.length === 0 ? (
                          <span className="text-xs text-ink-faint">No standing permissions (access comes from assignment, e.g. as a control/exception owner)</span>
                        ) : (
                          role.permissions.map((p) => (
                            <span key={p} className="rounded bg-bg px-1.5 py-0.5 font-mono text-[11px] text-ink-soft">
                              {p}
                            </span>
                          ))
                        )}
                      </div>
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-6 rounded-lg border border-line bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">All permissions</h2>
        <ul className="mt-3 grid gap-2 sm:grid-cols-2">
          {permissions.map((permission) => (
            <li key={permission.permission_id} className="border-t border-line pt-2 first:border-t-0 first:pt-0 sm:border-t-0 sm:pt-0">
              <div className="font-mono text-sm text-ink">{permission.permission_name}</div>
              <div className="text-xs text-ink-soft">{permission.description}</div>
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-6 rounded-lg border border-line bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">Segregation of duties</h2>
        <p className="mt-1 text-xs text-ink-soft max-w-3xl">
          Every workflow below is enforced by checking that the maker and the checker are two different <em>people</em>
          (by user identity), not by two different <em>permissions</em> — every role above that holds{' '}
          <code className="rounded bg-bg px-1 py-0.5 font-mono">audit_framework:manage</code> can currently act as either
          the maker or the checker on any of these, just never both for the same item. Splitting this into dedicated
          maker/reviewer permissions per role is planned but not yet built — until then, this table is the authoritative
          answer to "who can approve what" rather than the permission list above.
        </p>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
                <th className="py-2 pr-4">Action</th>
                <th className="py-2 pr-4">States</th>
                <th className="py-2 pr-4">Maker</th>
                <th className="py-2 pr-4">Checker</th>
                <th className="py-2 pr-4">Enforcement</th>
                <th className="py-2 pr-4">Required?</th>
              </tr>
            </thead>
            <tbody>
              {workflows.map((w) => (
                <tr key={w.action} className="border-t border-line align-top">
                  <td className="py-2 pr-4 font-medium text-ink whitespace-nowrap">{w.action}</td>
                  <td className="py-2 pr-4 text-xs text-ink-soft">
                    <div className="flex flex-wrap gap-1">
                      {w.states.map((s, i) => (
                        <span key={s} className="whitespace-nowrap font-mono">
                          {s}
                          {i < w.states.length - 1 && <span className="mx-1 text-ink-faint">→</span>}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="py-2 pr-4 text-xs text-ink-soft max-w-[16rem]">{w.maker}</td>
                  <td className="py-2 pr-4 text-xs text-ink-soft max-w-[16rem]">{w.checker}</td>
                  <td className="py-2 pr-4 text-xs text-ink-soft max-w-[20rem]">{w.enforcement}</td>
                  <td className="py-2 pr-4">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${
                        w.mandatory ? 'bg-emerald-100 text-emerald-800' : 'bg-bg text-ink-soft'
                      }`}
                    >
                      {w.mandatory ? 'Always enforced' : 'Org opt-in'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
