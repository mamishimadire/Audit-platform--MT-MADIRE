import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { AUDIT_FRAMEWORK_ROLES } from '../auth/permissions'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { RuleParametersOut } from '../types/api'

/** One plain-English line per shipped parameter — kept here rather than on
 * the backend since it's purely a UI label, not something the rule engine
 * or resolution logic needs. If an organization's stored parameters ever
 * contain a key not listed here (a newly shipped one this UI hasn't been
 * updated for yet), it still renders — just without a description. */
const DESCRIPTIONS: Record<string, string> = {
  dormancy_days: "How many days a user account can go without logging in before rules flag it as dormant.",
  password_expiry_days: "How many days a password is allowed to go without being changed before rules flag it as overdue.",
  access_review_sla_days: "How many days reviewers get to complete an access review before rules flag it as overdue.",
  termination_deprovision_sla_days: "How many days IT gets to remove a terminated employee's access before rules flag it as overdue.",
  certificate_expiry_warning_days: "How many days before a certificate expires that rules start warning about it.",
  patch_deployment_sla_days: "How many days a device gets to install a required security patch before rules flag it as overdue.",
  finding_remediation_sla_days: "How many days an owner gets to fix an audit finding before rules flag it as overdue.",
  generic_approval_limit: "The money amount above which a transaction needs extra approval, for controls that don't have their own specific limit.",
}

function humanize(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function RuleParametersPage() {
  const { hasRole } = useAuth()
  const canManage = hasRole(...AUDIT_FRAMEWORK_ROLES)
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [parameters, setParameters] = useState<Record<string, number> | null>(null)
  const [edited, setEdited] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const load = (orgId: string) => {
    apiClient.get<RuleParametersOut>(`/organizations/${orgId}/rule-parameters`).then((res) => {
      setParameters(res.data.parameters)
      setEdited({})
    })
  }

  useEffect(() => {
    if (organizationId) load(organizationId)
  }, [organizationId])

  const save = async () => {
    if (!organizationId) return
    const updates: Record<string, number> = {}
    for (const [key, raw] of Object.entries(edited)) {
      const n = Number(raw)
      if (!Number.isNaN(n)) updates[key] = n
    }
    if (Object.keys(updates).length === 0) return
    setSaving(true)
    setError(null)
    try {
      const res = await apiClient.put<RuleParametersOut>(`/organizations/${organizationId}/rule-parameters`, { parameters: updates })
      setParameters(res.data.parameters)
      setEdited({})
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not save these changes.')
    } finally {
      setSaving(false)
    }
  }

  const hasChanges = Object.keys(edited).length > 0

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Rule Parameters</h1>
      <p className="mt-1 max-w-2xl text-sm text-ink-soft">
        Some rules compare against a number instead of a fixed rule — like "how many days is too many". These are those
        numbers. Change one here and every rule that uses it updates automatically, on its next run — nothing needs to be
        rebuilt or re-approved.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      {!parameters ? (
        <p className="mt-6 text-sm text-ink-soft">Loading…</p>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
                <th className="px-4 py-2">Setting</th>
                <th className="px-4 py-2">What it does</th>
                <th className="px-4 py-2">Current value</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(parameters)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([key, value]) => (
                  <tr key={key} className="border-t border-line align-top">
                    <td className="px-4 py-2 font-mono text-xs text-ink">{key}</td>
                    <td className="max-w-md px-4 py-2 text-xs text-ink-soft">{DESCRIPTIONS[key] ?? `Adjustable setting: ${humanize(key)}.`}</td>
                    <td className="px-4 py-2">
                      {canManage ? (
                        <input
                          type="number"
                          value={edited[key] ?? String(value)}
                          onChange={(e) => setEdited({ ...edited, [key]: e.target.value })}
                          className="w-24 rounded-md border border-line px-2 py-1 text-sm tabular-nums"
                        />
                      ) : (
                        <span className="tabular-nums text-ink">{value}</span>
                      )}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}

      {canManage && parameters && (
        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={save}
            disabled={!hasChanges || saving}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {saving ? 'Saving…' : 'Save changes'}
          </button>
          {saved && <span className="text-xs font-medium text-accent-ink">Saved ✓</span>}
          {error && <span className="text-xs text-red-600">{error}</span>}
        </div>
      )}
    </div>
  )
}
