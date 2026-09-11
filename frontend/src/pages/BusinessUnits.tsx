import { useEffect, useState, type FormEvent } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { BusinessUnitOut } from '../types/api'

export function BusinessUnitsPage() {
  const { hasRole } = useAuth()
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [units, setUnits] = useState<BusinessUnitOut[]>([])
  const [form, setForm] = useState({ business_unit_name: '', business_unit_code: '' })
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Platform Admin was previously included here but does not actually hold
  // business_units:manage on the backend (only Platform Super Admin, Audit
  // Manager, Client Organisation Admin do) — narrowed to match reality.
  const canManage = hasRole('Platform Super Admin', 'Audit Manager', 'Client Organisation Admin')

  const load = (orgId: string) =>
    apiClient.get<BusinessUnitOut[]>(`/organizations/${orgId}/business-units`).then((res) => setUnits(res.data))

  useEffect(() => {
    if (organizationId) load(organizationId)
  }, [organizationId])

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!organizationId) return
    setError(null)
    setIsSubmitting(true)
    try {
      await apiClient.post(`/organizations/${organizationId}/business-units`, form)
      setForm({ business_unit_name: '', business_unit_code: '' })
      load(organizationId)
    } catch {
      setError('Could not create the business unit.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Business Units</h1>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Name</th>
              <th className="px-4 py-2">Code</th>
              <th className="px-4 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {units.map((u) => (
              <tr key={u.business_unit_id} className="border-t border-line">
                <td className="px-4 py-2 font-medium text-ink">{u.business_unit_name}</td>
                <td className="px-4 py-2 text-ink-soft">{u.business_unit_code ?? '—'}</td>
                <td className="px-4 py-2 text-ink-soft">{u.status}</td>
              </tr>
            ))}
            {units.length === 0 && (
              <tr>
                <td colSpan={3} className="px-4 py-6 text-center text-ink-soft">
                  No business units yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {canManage && organizationId && (
        <form onSubmit={handleSubmit} className="mt-6 max-w-md rounded-lg border border-line bg-surface p-5">
          <h2 className="text-sm font-semibold text-ink">Add a business unit</h2>
          <div className="mt-4 space-y-3">
            <input
              required
              placeholder="Name (e.g. Finance)"
              value={form.business_unit_name}
              onChange={(e) => setForm({ ...form, business_unit_name: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            />
            <input
              placeholder="Code (optional)"
              value={form.business_unit_code}
              onChange={(e) => setForm({ ...form, business_unit_code: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            />
          </div>
          {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={isSubmitting}
            className="mt-4 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {isSubmitting ? 'Creating…' : 'Create business unit'}
          </button>
        </form>
      )}
    </div>
  )
}
