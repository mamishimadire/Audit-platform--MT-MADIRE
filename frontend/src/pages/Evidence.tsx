import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { EvidenceOut } from '../types/api'

type HistoryPreset = 'today' | 'week' | 'month' | 'all' | 'custom'

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

export function EvidencePage() {
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [evidence, setEvidence] = useState<EvidenceOut[]>([])
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  // Same Current(today)/History(range) shape as Executions and the Audit
  // Trail — "Current" answers "what's been captured today," History is
  // everything else with a date range to narrow it down.
  const [view, setView] = useState<'current' | 'history'>('current')
  const [historyPreset, setHistoryPreset] = useState<HistoryPreset>('today')
  const [fromDate, setFromDate] = useState(() => isoDate(new Date()))
  const [toDate, setToDate] = useState(() => isoDate(new Date()))
  const [controlFilter, setControlFilter] = useState('')

  const applyPreset = (preset: HistoryPreset) => {
    setHistoryPreset(preset)
    const now = new Date()
    if (preset === 'today') {
      setFromDate(isoDate(now))
      setToDate(isoDate(now))
    } else if (preset === 'week') {
      const start = new Date(now)
      start.setDate(start.getDate() - 6)
      setFromDate(isoDate(start))
      setToDate(isoDate(now))
    } else if (preset === 'month') {
      const start = new Date(now.getFullYear(), now.getMonth(), 1)
      setFromDate(isoDate(start))
      setToDate(isoDate(now))
    } else if (preset === 'all') {
      setFromDate('')
      setToDate('')
    }
  }

  useEffect(() => {
    if (!organizationId) return
    setLoading(true)
    setError(false)
    const params: Record<string, string> = {}
    const effectiveFrom = view === 'current' ? isoDate(new Date()) : fromDate
    const effectiveTo = view === 'current' ? isoDate(new Date()) : toDate
    if (effectiveFrom) params.from_date = effectiveFrom
    if (effectiveTo) params.to_date = effectiveTo
    apiClient
      .get<EvidenceOut[]>(`/organizations/${organizationId}/evidence`, { params })
      .then((res) => setEvidence(res.data))
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [organizationId, view, fromDate, toDate])

  const controls = [...new Set(evidence.map((e) => e.control_code).filter((c): c is string => !!c))].sort()

  const sorted = [...evidence]
    .filter((e) => !controlFilter || e.control_code === controlFilter)
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Evidence</h1>
      <p className="mt-1 text-sm text-ink-soft">
        A hashed evidence record is captured automatically for every test execution — the hash proves the summary hasn't
        been altered after the fact.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}
      {error && <p className="mt-4 text-sm text-red-600">Could not load evidence for this organization.</p>}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <div className="flex gap-1 rounded-md bg-bg p-0.5 text-xs">
          <button
            onClick={() => setView('current')}
            className={`rounded px-3 py-1.5 font-medium ${view === 'current' ? 'bg-surface text-ink shadow-sm' : 'text-ink-soft'}`}
          >
            Current
          </button>
          <button
            onClick={() => setView('history')}
            className={`rounded px-3 py-1.5 font-medium ${view === 'history' ? 'bg-surface text-ink shadow-sm' : 'text-ink-soft'}`}
          >
            History
          </button>
        </div>
        {view === 'history' && (
          <>
            <div className="flex gap-1 rounded-md bg-bg p-0.5 text-xs">
              {(
                [
                  { key: 'today', label: 'Today' },
                  { key: 'week', label: 'This week' },
                  { key: 'month', label: 'This month' },
                  { key: 'all', label: 'All time' },
                ] as const
              ).map((p) => (
                <button
                  key={p.key}
                  onClick={() => applyPreset(p.key)}
                  className={`rounded px-3 py-1.5 font-medium ${historyPreset === p.key ? 'bg-surface text-ink shadow-sm' : 'text-ink-soft'}`}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-1 text-xs text-ink-soft">
              From
              <input
                type="date"
                value={fromDate}
                onChange={(e) => {
                  setHistoryPreset('custom')
                  setFromDate(e.target.value)
                }}
                className="rounded-md border border-line px-2 py-1 text-xs"
              />
            </label>
            <label className="flex items-center gap-1 text-xs text-ink-soft">
              To
              <input
                type="date"
                value={toDate}
                onChange={(e) => {
                  setHistoryPreset('custom')
                  setToDate(e.target.value)
                }}
                className="rounded-md border border-line px-2 py-1 text-xs"
              />
            </label>
          </>
        )}
        <select value={controlFilter} onChange={(e) => setControlFilter(e.target.value)} className="rounded-md border border-line px-2 py-1 text-xs">
          <option value="">All controls</option>
          {controls.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>

      <div className="mt-3 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Captured</th>
              <th className="px-4 py-2">What happened</th>
              <th className="px-4 py-2">Hash</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((e) => (
              <tr key={e.evidence_id} className="border-t border-line align-top">
                <td className="whitespace-nowrap px-4 py-2 text-ink-soft">{new Date(e.created_at).toLocaleString()}</td>
                <td className="px-4 py-2 text-ink">{e.summary ?? e.evidence_type ?? '—'}</td>
                <td className="px-4 py-2 font-mono text-xs text-ink-soft">{e.evidence_hash?.slice(0, 16) ?? '—'}…</td>
                <td className="px-4 py-2 text-right">
                  <button
                    onClick={() => setExpandedId(expandedId === e.evidence_id ? null : e.evidence_id)}
                    className="text-xs font-medium text-accent-ink hover:underline"
                  >
                    {expandedId === e.evidence_id ? 'Hide' : 'View raw'}
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
            {loading && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-ink-soft">
                  Loading evidence…
                </td>
              </tr>
            )}
            {!loading && !error && sorted.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-ink-soft">
                  No evidence captured {view === 'current' ? 'today' : 'in this range'}.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
