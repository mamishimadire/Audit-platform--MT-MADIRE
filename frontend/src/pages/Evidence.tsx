import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { EvidenceOut } from '../types/api'

export function EvidencePage() {
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [evidence, setEvidence] = useState<EvidenceOut[]>([])
  const [expandedId, setExpandedId] = useState<string | null>(null)

  useEffect(() => {
    if (!organizationId) return
    apiClient.get<EvidenceOut[]>(`/organizations/${organizationId}/evidence`).then((res) => setEvidence(res.data))
  }, [organizationId])

  const sorted = [...evidence].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Evidence</h1>
      <p className="mt-1 text-sm text-ink-soft">
        A hashed evidence record is captured automatically for every test execution — the hash proves the summary hasn't
        been altered after the fact.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Captured</th>
              <th className="px-4 py-2">Type</th>
              <th className="px-4 py-2">Hash</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((e) => (
              <tr key={e.evidence_id} className="border-t border-line align-top">
                <td className="whitespace-nowrap px-4 py-2 text-ink-soft">{new Date(e.created_at).toLocaleString()}</td>
                <td className="px-4 py-2 text-ink-soft">{e.evidence_type ?? '—'}</td>
                <td className="px-4 py-2 font-mono text-xs text-ink-soft">{e.evidence_hash?.slice(0, 16) ?? '—'}…</td>
                <td className="px-4 py-2 text-right">
                  <button
                    onClick={() => setExpandedId(expandedId === e.evidence_id ? null : e.evidence_id)}
                    className="text-xs font-medium text-accent-ink hover:underline"
                  >
                    {expandedId === e.evidence_id ? 'Hide' : 'View'}
                  </button>
                </td>
              </tr>
            ))}
            {expandedId &&
              sorted
                .filter((e) => e.evidence_id === expandedId)
                .map((e) => (
                  <tr key={`${e.evidence_id}-detail`}>
                    <td colSpan={4} className="bg-bg px-6 py-3">
                      <pre className="overflow-x-auto rounded bg-surface p-2 font-mono text-xs">{e.evidence_location}</pre>
                    </td>
                  </tr>
                ))}
            {sorted.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-ink-soft">
                  No evidence captured yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
