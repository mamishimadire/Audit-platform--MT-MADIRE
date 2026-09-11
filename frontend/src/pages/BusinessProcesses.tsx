import { useEffect, useState, type FormEvent } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { BusinessProcessOut } from '../types/api'

export function BusinessProcessesPage() {
  const { hasRole } = useAuth()
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [processes, setProcesses] = useState<BusinessProcessOut[]>([])
  const [form, setForm] = useState({ process_name: '', process_code: '', description: '' })
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const canManage = hasRole('Platform Super Admin', 'Audit Manager', 'Auditor', 'IT/Audit Technical User', 'Compliance Manager')

  const load = (orgId: string) =>
    apiClient.get<BusinessProcessOut[]>(`/organizations/${orgId}/business-processes`).then((res) => setProcesses(res.data))

  useEffect(() => {
    if (organizationId) load(organizationId)
  }, [organizationId])

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!organizationId) return
    setError(null)
    setIsSubmitting(true)
    try {
      await apiClient.post(`/organizations/${organizationId}/business-processes`, form)
      setForm({ process_name: '', process_code: '', description: '' })
      load(organizationId)
    } catch {
      setError('Could not create the business process.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Business Processes</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Risks and controls attach to a process — e.g. Procure-to-Pay, Payroll, Financial Reporting.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Name</th>
              <th className="px-4 py-2">Code</th>
              <th className="px-4 py-2">Description</th>
              <th className="px-4 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {processes.map((p) => (
              <tr key={p.process_id} className="border-t border-line">
                <td className="px-4 py-2 font-medium text-ink">{p.process_name}</td>
                <td className="px-4 py-2 text-ink-soft">{p.process_code ?? '—'}</td>
                <td className="px-4 py-2 text-ink-soft">{p.description ?? '—'}</td>
                <td className="px-4 py-2 text-ink-soft">{p.status}</td>
              </tr>
            ))}
            {processes.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-ink-soft">
                  No business processes yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {canManage && organizationId && (
        <form onSubmit={handleSubmit} className="mt-6 max-w-md rounded-lg border border-line bg-surface p-5">
          <h2 className="text-sm font-semibold text-ink">Add a business process</h2>
          <div className="mt-4 space-y-3">
            <input
              required
              placeholder="Name (e.g. Procure-to-Pay)"
              value={form.process_name}
              onChange={(e) => setForm({ ...form, process_name: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            />
            <input
              placeholder="Code (optional)"
              value={form.process_code}
              onChange={(e) => setForm({ ...form, process_code: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            />
            <textarea
              placeholder="Description"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
              rows={2}
            />
          </div>
          {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={isSubmitting}
            className="mt-4 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {isSubmitting ? 'Creating…' : 'Create business process'}
          </button>
        </form>
      )}
    </div>
  )
}
