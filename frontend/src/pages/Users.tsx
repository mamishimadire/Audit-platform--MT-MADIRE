import { useEffect, useState, type FormEvent } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import type { OrganizationOut, RoleOut, UserOut } from '../types/api'

const emptyForm = { first_name: '', last_name: '', email: '', role_names: [] as string[] }

function TemporaryPasswordCell({ password }: { password: string | null }) {
  const [copied, setCopied] = useState(false)
  if (!password) return <span className="text-ink-soft">—</span>
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(password)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard access denied — the password is still visible to select manually
    }
  }
  return (
    <div className="flex items-center gap-2">
      <code className="rounded-md bg-accent-soft px-2 py-1 font-mono text-xs text-accent-ink">{password}</code>
      <button onClick={copy} className="whitespace-nowrap rounded-md bg-accent px-2 py-1 text-xs font-medium text-white">
        {copied ? 'Copied ✓' : 'Copy'}
      </button>
    </div>
  )
}

const STATUS_LABELS: Record<string, { label: string; tone: string }> = {
  pending_approval: { label: 'Pending approval', tone: 'bg-orange-50 text-orange-700' },
  pending: { label: 'Pending activation', tone: 'bg-purple-50 text-purple-700' },
  active: { label: 'Active', tone: 'bg-accent-soft text-accent-ink' },
  inactive: { label: 'Inactive', tone: 'bg-bg text-ink-soft' },
  locked: { label: 'Locked', tone: 'bg-red-50 text-red-700' },
  rejected: { label: 'Rejected', tone: 'bg-red-50 text-red-700' },
}

function StatusBadge({ status }: { status: string }) {
  const { label, tone } = STATUS_LABELS[status] ?? { label: status, tone: 'bg-bg text-ink-soft' }
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}>{label}</span>
}

function UsersTable({
  users,
  currentUserId,
  canApprove,
  onApprove,
  onReject,
}: {
  users: UserOut[]
  currentUserId: string | undefined
  canApprove: boolean
  onApprove: (userId: string) => void
  onReject: (userId: string) => void
}) {
  return (
    <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
            <th className="px-4 py-2">Name</th>
            <th className="px-4 py-2">Email</th>
            <th className="px-4 py-2">Roles</th>
            <th className="px-4 py-2">Status</th>
            <th className="px-4 py-2">Temporary password</th>
            <th className="px-4 py-2"></th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => {
            const isOwnAddition = !!currentUserId && u.created_by === currentUserId
            return (
              <tr key={u.user_id} className="border-t border-line">
                <td className="px-4 py-2 font-medium text-ink">
                  {u.first_name} {u.last_name}
                </td>
                <td className="px-4 py-2 text-ink-soft">{u.email}</td>
                <td className="px-4 py-2 text-ink-soft">{u.roles.length > 0 ? u.roles.join(', ') : '—'}</td>
                <td className="px-4 py-2">
                  <StatusBadge status={u.status} />
                </td>
                <td className="px-4 py-2">
                  <TemporaryPasswordCell password={u.temporary_password} />
                </td>
                <td className="px-4 py-2">
                  {u.status === 'pending_approval' && canApprove && (
                    isOwnAddition ? (
                      <span className="text-xs text-ink-faint" title="You added this user — someone else must approve them">
                        Awaiting another approver
                      </span>
                    ) : (
                      <div className="flex gap-2">
                        <button
                          onClick={() => onApprove(u.user_id)}
                          className="rounded-md bg-accent px-2 py-1 text-xs font-medium text-white"
                        >
                          Approve
                        </button>
                        <button
                          onClick={() => onReject(u.user_id)}
                          className="rounded-md border border-line px-2 py-1 text-xs font-medium text-ink hover:bg-bg"
                        >
                          Reject
                        </button>
                      </div>
                    )
                  )}
                </td>
              </tr>
            )
          })}
          {users.length === 0 && (
            <tr>
              <td colSpan={6} className="px-4 py-6 text-center text-ink-soft">
                No users yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

export function UsersPage() {
  const { user, hasRole } = useAuth()
  const isPlatformUser = !user?.organization_id

  const [view, setView] = useState<'client' | 'internal'>('client')
  const [organizations, setOrganizations] = useState<OrganizationOut[]>([])
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(user?.organization_id ?? null)
  const [clientUsers, setClientUsers] = useState<UserOut[]>([])
  const [internalUsers, setInternalUsers] = useState<UserOut[]>([])
  const [allRoles, setAllRoles] = useState<RoleOut[]>([])
  const [form, setForm] = useState(emptyForm)
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  // A client user sees only their own organization's role category — this
  // never includes an internal/platform role, and no internal-team tab is
  // ever shown to them. Internal (Madire) staff additionally see their own
  // platform role category, kept in a completely separate tab.
  const clientRoles = allRoles.filter((r) => r.role_scope === 'client')
  const platformRoles = allRoles.filter((r) => r.role_scope === 'platform')

  const canManageClientUsers = hasRole('Platform Super Admin', 'Platform Admin', 'Audit Manager', 'Client Organisation Admin')
  const canManageInternalUsers = hasRole('Platform Super Admin', 'Platform Admin')

  useEffect(() => {
    if (isPlatformUser) {
      apiClient.get<OrganizationOut[]>('/organizations').then((res) => {
        setOrganizations(res.data)
        if (!selectedOrgId && res.data.length > 0) setSelectedOrgId(res.data[0].organization_id)
      })
    }
    apiClient.get<RoleOut[]>('/reference/roles').then((res) => setAllRoles(res.data))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user])

  const loadClientUsers = (orgId: string) => apiClient.get<UserOut[]>(`/organizations/${orgId}/users`).then((res) => setClientUsers(res.data))
  const loadInternalUsers = () => apiClient.get<UserOut[]>('/platform/users').then((res) => setInternalUsers(res.data))

  useEffect(() => {
    if (selectedOrgId) loadClientUsers(selectedOrgId)
  }, [selectedOrgId])

  useEffect(() => {
    if (isPlatformUser && view === 'internal') loadInternalUsers()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view])

  const toggleRole = (roleName: string) => {
    setForm((f) => ({
      ...f,
      role_names: f.role_names.includes(roleName) ? f.role_names.filter((r) => r !== roleName) : [...f.role_names, roleName],
    }))
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setIsSubmitting(true)
    try {
      if (view === 'internal') {
        await apiClient.post('/platform/users', form)
        setForm(emptyForm)
        loadInternalUsers()
      } else {
        if (!selectedOrgId) return
        await apiClient.post(`/organizations/${selectedOrgId}/users`, form)
        setForm(emptyForm)
        loadClientUsers(selectedOrgId)
      }
    } catch {
      setError('Could not create the user — check the fields and try again.')
    } finally {
      setIsSubmitting(false)
    }
  }

  const roleChecklist = view === 'internal' ? platformRoles : clientRoles

  const approveUser = async (userId: string) => {
    const path = view === 'internal' ? `/platform/users/${userId}/approve` : `/organizations/${selectedOrgId}/users/${userId}/approve`
    try {
      await apiClient.post(path)
      view === 'internal' ? loadInternalUsers() : selectedOrgId && loadClientUsers(selectedOrgId)
    } catch {
      setError('Could not approve this user — try again.')
    }
  }

  const rejectUser = async (userId: string) => {
    const reason = window.prompt('Why are you rejecting this user? This is recorded in the audit trail.')
    if (!reason || !reason.trim()) return
    const path = view === 'internal' ? `/platform/users/${userId}/reject` : `/organizations/${selectedOrgId}/users/${userId}/reject`
    try {
      await apiClient.post(path, { reason })
      view === 'internal' ? loadInternalUsers() : selectedOrgId && loadClientUsers(selectedOrgId)
    } catch {
      setError('Could not reject this user — try again.')
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Users</h1>
      <p className="mt-1 text-sm text-ink-soft">
        The system always generates the temporary password — it stays visible here, copyable, until each person activates their own
        account and sets their own password.
      </p>

      {isPlatformUser && (
        <div className="mt-4 flex gap-2 border-b border-line">
          <button
            onClick={() => setView('client')}
            className={`px-3 py-2 text-sm font-medium ${view === 'client' ? 'border-b-2 border-accent text-accent-ink' : 'text-ink-soft'}`}
          >
            Client organization users
          </button>
          <button
            onClick={() => setView('internal')}
            className={`px-3 py-2 text-sm font-medium ${view === 'internal' ? 'border-b-2 border-accent text-accent-ink' : 'text-ink-soft'}`}
          >
            Our internal team
          </button>
        </div>
      )}

      {view === 'client' && isPlatformUser && (
        <div className="mt-3">
          <label className="text-xs font-medium uppercase tracking-wide text-ink-soft">Organization</label>
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
        </div>
      )}

      {view === 'client' ? (
        <UsersTable
          users={clientUsers}
          currentUserId={user?.user_id}
          canApprove={canManageClientUsers}
          onApprove={approveUser}
          onReject={rejectUser}
        />
      ) : (
        <UsersTable
          users={internalUsers}
          currentUserId={user?.user_id}
          canApprove={canManageInternalUsers}
          onApprove={approveUser}
          onReject={rejectUser}
        />
      )}

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      {((view === 'client' && canManageClientUsers && selectedOrgId) || (view === 'internal' && canManageInternalUsers)) && (
        <form onSubmit={handleSubmit} className="mt-6 max-w-md rounded-lg border border-line bg-surface p-5">
          <h2 className="text-sm font-semibold text-ink">
            {view === 'internal' ? 'Add one of our internal team' : 'Add a user to this organization'}
          </h2>
          <div className="mt-4 space-y-3">
            <input
              required
              placeholder="First name"
              value={form.first_name}
              onChange={(e) => setForm({ ...form, first_name: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            />
            <input
              required
              placeholder="Last name"
              value={form.last_name}
              onChange={(e) => setForm({ ...form, last_name: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            />
            <input
              required
              type="email"
              placeholder="Email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            />
            {roleChecklist.length > 0 && (
              <div>
                <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Role(s)</div>
                <div className="mt-1 max-h-40 space-y-1 overflow-y-auto rounded-md border border-line p-2">
                  {roleChecklist.map((r) => (
                    <label key={r.role_id} className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={form.role_names.includes(r.role_name)} onChange={() => toggleRole(r.role_name)} />
                      {r.role_name}
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>
          <button
            type="submit"
            disabled={isSubmitting}
            className="mt-4 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {isSubmitting ? 'Creating…' : 'Create user'}
          </button>
        </form>
      )}
    </div>
  )
}
