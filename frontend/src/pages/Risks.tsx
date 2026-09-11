import { useEffect, useState, type FormEvent } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { BusinessProcessOut, RiskCategoryOut, RiskOut, RiskRating } from '../types/api'

const RATING_STYLES: Record<string, string> = {
  low: 'bg-accent-soft text-accent-ink',
  medium: 'bg-bg text-ink-soft',
  high: 'bg-orange-50 text-orange-700',
  critical: 'bg-red-50 text-red-700',
}

function RatingBadge({ rating }: { rating: string | null }) {
  if (!rating) return <span className="text-ink-soft">—</span>
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${RATING_STYLES[rating] ?? ''}`}>{rating}</span>
  )
}

type EditForm = {
  risk_name: string
  risk_description: string
  risk_category_id: string
  inherent_risk_rating: RiskRating
}

export function RisksPage() {
  const { user, hasRole } = useAuth()
  const isPlatformUser = !user?.organization_id
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [risks, setRisks] = useState<RiskOut[]>([])
  const [categories, setCategories] = useState<RiskCategoryOut[]>([])
  const [processes, setProcesses] = useState<BusinessProcessOut[]>([])
  const [form, setForm] = useState({
    risk_name: '',
    risk_description: '',
    risk_category_id: '',
    process_id: '',
    inherent_risk_rating: '' as RiskRating,
  })
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [editingRiskId, setEditingRiskId] = useState<string | null>(null)
  const [editForm, setEditForm] = useState<EditForm | null>(null)
  const [isSaving, setIsSaving] = useState(false)

  const canManage = hasRole('Platform Super Admin', 'Audit Manager', 'Auditor', 'IT/Audit Technical User', 'Compliance Manager')

  useEffect(() => {
    apiClient.get<RiskCategoryOut[]>('/reference/risk-categories').then((res) => setCategories(res.data))
  }, [])

  const load = (orgId: string) => {
    apiClient.get<RiskOut[]>(`/organizations/${orgId}/risks`).then((res) => setRisks(res.data))
    apiClient.get<BusinessProcessOut[]>(`/organizations/${orgId}/business-processes`).then((res) => setProcesses(res.data))
  }

  useEffect(() => {
    if (organizationId) load(organizationId)
  }, [organizationId])

  const categoryName = (id: string | null) => categories.find((c) => c.risk_category_id === id)?.category_name ?? '—'
  const processName = (id: string | null) => processes.find((p) => p.process_id === id)?.process_name ?? '—'

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!organizationId) return
    setError(null)
    setIsSubmitting(true)
    try {
      await apiClient.post(`/organizations/${organizationId}/risks`, {
        risk_name: form.risk_name,
        risk_description: form.risk_description || null,
        risk_category_id: form.risk_category_id || null,
        process_id: form.process_id || null,
        inherent_risk_rating: form.inherent_risk_rating || null,
      })
      setForm({ risk_name: '', risk_description: '', risk_category_id: '', process_id: '', inherent_risk_rating: '' })
      load(organizationId)
    } catch {
      setError('Could not create the risk.')
    } finally {
      setIsSubmitting(false)
    }
  }

  const startEdit = (risk: RiskOut) => {
    setEditingRiskId(risk.risk_id)
    setEditForm({
      risk_name: risk.risk_name,
      risk_description: risk.risk_description ?? '',
      risk_category_id: risk.risk_category_id ?? '',
      inherent_risk_rating: (risk.inherent_risk_rating ?? '') as RiskRating,
    })
  }

  const cancelEdit = () => {
    setEditingRiskId(null)
    setEditForm(null)
  }

  const saveEdit = async (riskId: string) => {
    if (!organizationId || !editForm) return
    setIsSaving(true)
    try {
      await apiClient.patch(`/organizations/${organizationId}/risks/${riskId}`, {
        risk_name: editForm.risk_name,
        risk_description: editForm.risk_description || null,
        risk_category_id: editForm.risk_category_id || null,
        inherent_risk_rating: editForm.inherent_risk_rating || null,
      })
      cancelEdit()
      load(organizationId)
    } catch {
      setError('Could not save changes to this risk.')
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Risks</h1>
      {isPlatformUser && (
        <p className="mt-1 text-sm text-ink-soft">
          Your starting risk register is auto-created from the industries selected when the organization was set up — edit any
          risk below to tailor it, or add your own.
        </p>
      )}
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Risk</th>
              <th className="px-4 py-2">Category</th>
              <th className="px-4 py-2">Process</th>
              <th className="px-4 py-2">Inherent</th>
              <th className="px-4 py-2">Residual</th>
              <th className="px-4 py-2">Control status</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {risks.map((r) =>
              editingRiskId === r.risk_id && editForm ? (
                <tr key={r.risk_id} className="border-t border-line bg-bg">
                  <td className="px-4 py-2">
                    <input
                      value={editForm.risk_name}
                      onChange={(e) => setEditForm({ ...editForm, risk_name: e.target.value })}
                      className="w-full rounded-md border border-line px-2 py-1 text-sm"
                    />
                    <textarea
                      value={editForm.risk_description}
                      onChange={(e) => setEditForm({ ...editForm, risk_description: e.target.value })}
                      rows={2}
                      className="mt-1 w-full rounded-md border border-line px-2 py-1 text-xs"
                      placeholder="Description"
                    />
                  </td>
                  <td className="px-4 py-2">
                    <select
                      value={editForm.risk_category_id}
                      onChange={(e) => setEditForm({ ...editForm, risk_category_id: e.target.value })}
                      className="w-full rounded-md border border-line px-2 py-1 text-sm"
                    >
                      <option value="">—</option>
                      {categories.map((c) => (
                        <option key={c.risk_category_id} value={c.risk_category_id}>
                          {c.category_name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-4 py-2 text-ink-soft">{processName(r.process_id)}</td>
                  <td className="px-4 py-2">
                    <select
                      value={editForm.inherent_risk_rating}
                      onChange={(e) => setEditForm({ ...editForm, inherent_risk_rating: e.target.value as RiskRating })}
                      className="w-full rounded-md border border-line px-2 py-1 text-sm"
                    >
                      <option value="">—</option>
                      <option value="low">Low</option>
                      <option value="medium">Medium</option>
                      <option value="high">High</option>
                      <option value="critical">Critical</option>
                    </select>
                  </td>
                  <td className="px-4 py-2"><RatingBadge rating={r.residual_risk_rating} /></td>
                  <td className="px-4 py-2 text-ink-soft">
                    {r.has_active_control ? (
                      <span className="rounded-full bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent-ink">Mitigated</span>
                    ) : (
                      <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">No active control</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <button
                      onClick={() => saveEdit(r.risk_id)}
                      disabled={isSaving}
                      className="mr-2 text-xs font-medium text-accent-ink hover:underline disabled:opacity-60"
                    >
                      {isSaving ? 'Saving…' : 'Save'}
                    </button>
                    <button onClick={cancelEdit} className="text-xs font-medium text-ink-soft hover:underline">
                      Cancel
                    </button>
                  </td>
                </tr>
              ) : (
                <tr key={r.risk_id} className={`border-t border-line ${r.has_active_control ? '' : 'opacity-60'}`}>
                  <td className="px-4 py-2 font-medium text-ink">{r.risk_name}</td>
                  <td className="px-4 py-2 text-ink-soft">{categoryName(r.risk_category_id)}</td>
                  <td className="px-4 py-2 text-ink-soft">{processName(r.process_id)}</td>
                  <td className="px-4 py-2"><RatingBadge rating={r.inherent_risk_rating} /></td>
                  <td className="px-4 py-2"><RatingBadge rating={r.residual_risk_rating} /></td>
                  <td className="px-4 py-2">
                    {r.has_active_control ? (
                      <span className="rounded-full bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent-ink">Mitigated</span>
                    ) : (
                      <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">No active control</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right">
                    {canManage && (
                      <button onClick={() => startEdit(r)} className="text-xs font-medium text-accent-ink hover:underline">
                        Edit
                      </button>
                    )}
                  </td>
                </tr>
              ),
            )}
            {risks.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-ink-soft">
                  No risks yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      {canManage && organizationId && (
        <form onSubmit={handleSubmit} className="mt-6 max-w-md rounded-lg border border-line bg-surface p-5">
          <h2 className="text-sm font-semibold text-ink">Add a risk</h2>
          <div className="mt-4 space-y-3">
            <input
              required
              placeholder="Risk statement"
              value={form.risk_name}
              onChange={(e) => setForm({ ...form, risk_name: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            />
            <textarea
              placeholder="Description"
              value={form.risk_description}
              onChange={(e) => setForm({ ...form, risk_description: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
              rows={2}
            />
            <select
              value={form.risk_category_id}
              onChange={(e) => setForm({ ...form, risk_category_id: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            >
              <option value="">Category (optional)</option>
              {categories.map((c) => (
                <option key={c.risk_category_id} value={c.risk_category_id}>
                  {c.category_name}
                </option>
              ))}
            </select>
            <select
              value={form.process_id}
              onChange={(e) => setForm({ ...form, process_id: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            >
              <option value="">Business process (optional)</option>
              {processes.map((p) => (
                <option key={p.process_id} value={p.process_id}>
                  {p.process_name}
                </option>
              ))}
            </select>
            <select
              value={form.inherent_risk_rating}
              onChange={(e) => setForm({ ...form, inherent_risk_rating: e.target.value as RiskRating })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            >
              <option value="">Inherent risk rating (optional)</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="critical">Critical</option>
            </select>
          </div>
          <button
            type="submit"
            disabled={isSubmitting}
            className="mt-4 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {isSubmitting ? 'Creating…' : 'Create risk'}
          </button>
        </form>
      )}
    </div>
  )
}
