import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { AUDIT_FRAMEWORK_ROLES } from '../auth/permissions'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { FindingOut, RemediationActionOut } from '../types/api'

const STATUS_OPTIONS = ['pending', 'in_progress', 'completed']

export function RemediationPage() {
  const { hasRole } = useAuth()
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [actions, setActions] = useState<RemediationActionOut[]>([])
  const [findings, setFindings] = useState<FindingOut[]>([])
  const [savingId, setSavingId] = useState<string | null>(null)

  // Client Organisation Admin / Exception Owner were previously included
  // here but hold no backend permission for this route (audit_framework:manage) —
  // narrowed to match reality rather than show a control that 403s. Giving
  // Exception Owner real, narrow remediation rights on their own assigned
  // items is tracked separately as part of the granular permission catalog.
  const canManage = hasRole(...AUDIT_FRAMEWORK_ROLES)

  const load = (orgId: string) => {
    apiClient.get<RemediationActionOut[]>(`/organizations/${orgId}/remediation-actions`).then((res) => setActions(res.data))
    apiClient.get<FindingOut[]>(`/organizations/${orgId}/findings`).then((res) => setFindings(res.data))
  }

  useEffect(() => {
    if (organizationId) load(organizationId)
  }, [organizationId])

  const findingTitle = (id: string) => findings.find((f) => f.finding_id === id)?.finding_title ?? id

  const updateStatus = async (remediationId: string, status: string) => {
    if (!organizationId) return
    setSavingId(remediationId)
    try {
      await apiClient.patch(`/remediation-actions/${remediationId}`, { status })
      load(organizationId)
    } finally {
      setSavingId(null)
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Remediation</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Every remediation action across this organization's findings. Marking one "completed" moves its finding to
        awaiting re-test — it does not close the finding by itself.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Finding</th>
              <th className="px-4 py-2">Action</th>
              <th className="px-4 py-2">Target date</th>
              <th className="px-4 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {actions.map((a) => (
              <tr key={a.remediation_id} className="border-t border-line">
                <td className="px-4 py-2 font-medium text-ink">{findingTitle(a.finding_id)}</td>
                <td className="px-4 py-2 text-ink-soft">{a.action_description}</td>
                <td className="px-4 py-2 text-ink-soft">
                  {a.target_date ? new Date(a.target_date).toLocaleDateString() : '—'}
                  {a.is_overdue && <span className="ml-2 rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-700">overdue</span>}
                </td>
                <td className="px-4 py-2">
                  {canManage ? (
                    <select
                      value={a.status}
                      onChange={(e) => updateStatus(a.remediation_id, e.target.value)}
                      disabled={savingId === a.remediation_id}
                      className="rounded-md border border-line px-2 py-1 text-xs"
                    >
                      {STATUS_OPTIONS.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span className="text-ink-soft">{a.status}</span>
                  )}
                </td>
              </tr>
            ))}
            {actions.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-ink-soft">
                  No remediation actions yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
