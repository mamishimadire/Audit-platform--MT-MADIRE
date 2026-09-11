import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { AuditTestOut, FindingOut, RetestOut } from '../types/api'

export function RetestsPage() {
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [retests, setRetests] = useState<RetestOut[]>([])
  const [findings, setFindings] = useState<FindingOut[]>([])
  const [tests, setTests] = useState<AuditTestOut[]>([])

  useEffect(() => {
    if (!organizationId) return
    apiClient.get<RetestOut[]>(`/organizations/${organizationId}/retests`).then((res) => setRetests(res.data))
    apiClient.get<FindingOut[]>(`/organizations/${organizationId}/findings`).then((res) => setFindings(res.data))
    apiClient.get<AuditTestOut[]>(`/organizations/${organizationId}/audit-tests`).then((res) => setTests(res.data))
  }, [organizationId])

  const findingTitle = (id: string) => findings.find((f) => f.finding_id === id)?.finding_title ?? id
  const testName = (id: string) => tests.find((t) => t.audit_test_id === id)?.test_name ?? id

  const sorted = [...retests].sort((a, b) => new Date(b.retest_date).getTime() - new Date(a.retest_date).getTime())

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Re-tests</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Every re-test performed across this organization's findings. A PASS closes the finding; a FAIL reopens it — never
        auto-closed on say-so alone.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Finding</th>
              <th className="px-4 py-2">Audit test</th>
              <th className="px-4 py-2">Date</th>
              <th className="px-4 py-2">Result</th>
              <th className="px-4 py-2">Comments</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.retest_id} className="border-t border-line">
                <td className="px-4 py-2 font-medium text-ink">{findingTitle(r.finding_id)}</td>
                <td className="px-4 py-2 text-ink-soft">{testName(r.audit_test_id)}</td>
                <td className="px-4 py-2 text-ink-soft">{new Date(r.retest_date).toLocaleString()}</td>
                <td className="px-4 py-2">
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                      r.result === 'pass' ? 'bg-accent-soft text-accent-ink' : 'bg-red-50 text-red-700'
                    }`}
                  >
                    {r.result ?? '—'}
                  </span>
                </td>
                <td className="px-4 py-2 text-ink-soft">{r.comments ?? '—'}</td>
              </tr>
            ))}
            {sorted.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-ink-soft">
                  No re-tests yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
