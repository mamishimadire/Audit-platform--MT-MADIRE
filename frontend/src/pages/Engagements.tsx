import { useEffect, useMemo, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import type { OrganizationOut, ScopeOut, UserOut } from '../types/api'

export function EngagementsPage() {
  const { hasRole } = useAuth()
  const canManage = hasRole('Platform Super Admin', 'Platform Admin')

  const [organizations, setOrganizations] = useState<OrganizationOut[]>([])
  const [internalUsers, setInternalUsers] = useState<UserOut[]>([])
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null)
  const [grants, setGrants] = useState<ScopeOut[]>([])
  const [selectedUserId, setSelectedUserId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  useEffect(() => {
    if (!canManage) return
    apiClient.get<OrganizationOut[]>('/scope-assignments/organizations').then((res) => {
      setOrganizations(res.data)
      if (!selectedOrgId && res.data.length > 0) setSelectedOrgId(res.data[0].organization_id)
    })
    apiClient.get<UserOut[]>('/platform/users').then((res) => setInternalUsers(res.data))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canManage])

  const loadGrants = (orgId: string) =>
    apiClient.get<ScopeOut[]>(`/organizations/${orgId}/scope-assignments`).then((res) => setGrants(res.data))

  useEffect(() => {
    if (selectedOrgId) loadGrants(selectedOrgId)
  }, [selectedOrgId])

  const userById = useMemo(() => new Map(internalUsers.map((u) => [u.user_id, u])), [internalUsers])

  const assignedUserIds = useMemo(() => new Set(grants.map((g) => g.user_id)), [grants])
  const assignableUsers = internalUsers.filter(
    (u) => !assignedUserIds.has(u.user_id) && !u.roles.includes('Platform Super Admin'),
  )

  const grant = async () => {
    if (!selectedOrgId || !selectedUserId) return
    setError(null)
    setIsSubmitting(true)
    try {
      await apiClient.post(`/organizations/${selectedOrgId}/scope-assignments`, { user_id: selectedUserId })
      setSelectedUserId('')
      loadGrants(selectedOrgId)
    } catch {
      setError('Could not grant access — they may already have it.')
    } finally {
      setIsSubmitting(false)
    }
  }

  const revoke = async (userId: string) => {
    if (!selectedOrgId) return
    await apiClient.delete(`/organizations/${selectedOrgId}/scope-assignments/${userId}`)
    loadGrants(selectedOrgId)
  }

  if (!canManage) {
    return (
      <div>
        <h1 className="text-2xl font-semibold text-ink">Engagements</h1>
        <p className="mt-2 text-sm text-ink-soft">Only a Platform Super Admin or Platform Admin can assign client engagements.</p>
      </div>
    )
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Engagements</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Assign our internal team to the client(s) they're engaged on. Platform Super Admin always sees every client; everyone
        else (Audit Manager, Auditor, IT/Audit Technical User, Compliance Manager, Support User) sees only the clients
        assigned here.
      </p>

      <div className="mt-4">
        <label className="text-xs font-medium uppercase tracking-wide text-ink-soft">Client</label>
        <select
          value={selectedOrgId ?? ''}
          onChange={(e) => setSelectedOrgId(e.target.value)}
          className="mt-1 block rounded-md border border-line px-3 py-2 text-sm"
        >
          {organizations.map((org) => (
            <option key={org.organization_id} value={org.organization_id}>
              {org.organization_name}
            </option>
          ))}
        </select>
        {organizations.length === 0 && <p className="mt-2 text-sm text-ink-soft">No client organizations yet.</p>}
      </div>

      {selectedOrgId && (
        <>
          <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
                  <th className="px-4 py-2">Name</th>
                  <th className="px-4 py-2">Email</th>
                  <th className="px-4 py-2">Role(s)</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {grants.map((g) => {
                  const u = userById.get(g.user_id)
                  return (
                    <tr key={g.scope_id} className="border-t border-line">
                      <td className="px-4 py-2 font-medium text-ink">{u ? `${u.first_name} ${u.last_name}` : g.user_id}</td>
                      <td className="px-4 py-2 text-ink-soft">{u?.email ?? '—'}</td>
                      <td className="px-4 py-2 text-ink-soft">{u && u.roles.length > 0 ? u.roles.join(', ') : '—'}</td>
                      <td className="px-4 py-2 text-right">
                        <button onClick={() => revoke(g.user_id)} className="text-xs font-medium text-red-600 hover:underline">
                          Revoke
                        </button>
                      </td>
                    </tr>
                  )
                })}
                {grants.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-6 text-center text-ink-soft">
                      Nobody is assigned to this client yet — only a Platform Super Admin can see it until you assign someone.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="mt-4 flex max-w-xl items-end gap-2 rounded-lg border border-line bg-surface p-4">
            <div className="flex-1">
              <label className="text-xs font-medium uppercase tracking-wide text-ink-soft">Assign someone from our team</label>
              <select
                value={selectedUserId}
                onChange={(e) => setSelectedUserId(e.target.value)}
                className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
              >
                <option value="">Select a team member…</option>
                {assignableUsers.map((u) => (
                  <option key={u.user_id} value={u.user_id}>
                    {u.first_name} {u.last_name} — {u.roles.join(', ') || 'no role'}
                  </option>
                ))}
              </select>
            </div>
            <button
              onClick={grant}
              disabled={!selectedUserId || isSubmitting}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              {isSubmitting ? 'Granting…' : 'Grant access'}
            </button>
          </div>
          {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
        </>
      )}
    </div>
  )
}
