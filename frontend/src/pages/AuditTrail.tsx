import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { AuditLogOut } from '../types/api'

export function AuditTrailPage() {
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [logs, setLogs] = useState<AuditLogOut[] | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    if (!organizationId) return
    setError(false)
    apiClient
      .get<AuditLogOut[]>(`/organizations/${organizationId}/audit-logs`)
      .then((res) => setLogs(res.data))
      .catch(() => setError(true))
  }, [organizationId])

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Audit Trail</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Every logged platform action for this organization — who, what, when. Protected from modification; this is a
        read-only view.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      {error && <p className="mt-4 text-sm text-red-600">You don't have permission to view this organization's audit trail.</p>}

      <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">When</th>
              <th className="px-4 py-2">Action</th>
              <th className="px-4 py-2">Entity</th>
              <th className="px-4 py-2">Change</th>
            </tr>
          </thead>
          <tbody>
            {logs?.map((log) => (
              <tr key={log.log_id} className="border-t border-line align-top">
                <td className="whitespace-nowrap px-4 py-2 text-xs text-ink-soft">{new Date(log.timestamp).toLocaleString()}</td>
                <td className="px-4 py-2 text-sm text-ink">{log.action}</td>
                <td className="px-4 py-2 font-mono text-xs text-ink-soft">{log.entity_type ?? '—'}</td>
                <td className="px-4 py-2">
                  {log.new_value && (
                    <pre className="overflow-x-auto rounded bg-bg p-1.5 font-mono text-xs">{JSON.stringify(log.new_value)}</pre>
                  )}
                </td>
              </tr>
            ))}
            {logs && logs.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-ink-soft">
                  No activity logged yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
