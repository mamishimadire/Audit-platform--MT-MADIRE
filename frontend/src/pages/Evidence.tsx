import { Fragment, useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import { ExportButton } from '../components/ExportButton'
import type { ExportReport } from '../lib/exportTable'
import type { EvidenceOut } from '../types/api'

type HistoryPreset = 'today' | 'week' | 'month' | 'all' | 'custom'

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

const EVIDENCE_FIELD_LABELS: Record<string, string> = {
  records_analyzed: 'Records analyzed',
  exceptions_found: 'Exceptions found',
  started_at: 'Started',
  completed_at: 'Completed',
  hostname: 'Device',
}

function titleCase(key: string): string {
  return key
    .replace(/_/g, ' ')
    .split(' ')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

function humanizeEvidenceValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if ((key === 'started_at' || key === 'completed_at') && typeof value === 'string') {
    const d = new Date(value)
    return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
  }
  return String(value)
}

// evidence_location's JSON shape differs by check type (a test result vs.
// a device/software compliance check) — this reads whichever shape is
// present and always produces plain-English rows, never raw JSON, the
// same reasoning as the backend's own _evidence_summary_sentence.
function humanizeEvidenceLocation(raw: string | null): { label: string; value: string }[] {
  if (!raw) return []
  let parsed: Record<string, unknown>
  try {
    parsed = JSON.parse(raw)
  } catch {
    return [{ label: 'Detail', value: raw }]
  }
  const rows: { label: string; value: string }[] = []
  const { started_at, completed_at, audit_test_id: _audit_test_id, checks, ...rest } = parsed as Record<string, unknown>
  for (const [key, value] of Object.entries(rest)) {
    if (key === 'hostname' && 'checks' in parsed) continue // shown as its own header below, not a row
    rows.push({ label: EVIDENCE_FIELD_LABELS[key] ?? titleCase(key), value: humanizeEvidenceValue(key, value) })
  }
  if (checks && typeof checks === 'object') {
    for (const [key, value] of Object.entries(checks as Record<string, unknown>)) {
      rows.push({ label: titleCase(key), value: humanizeEvidenceValue(key, value) })
    }
  }
  if (typeof started_at === 'string') rows.push({ label: 'Started', value: humanizeEvidenceValue('started_at', started_at) })
  if (typeof completed_at === 'string') rows.push({ label: 'Completed', value: humanizeEvidenceValue('completed_at', completed_at) })
  if (typeof started_at === 'string' && typeof completed_at === 'string') {
    const ms = new Date(completed_at).getTime() - new Date(started_at).getTime()
    if (Number.isFinite(ms) && ms >= 0) rows.push({ label: 'Duration', value: ms < 1000 ? '<1 second' : `${Math.round(ms / 1000)} seconds` })
  }
  return rows
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

  const orgName = organizations.find((o) => o.organization_id === organizationId)?.organization_name ?? ''
  const scopeLabel =
    view === 'current' ? 'Today' : historyPreset === 'all' ? 'All time' : `${fromDate || '…'} to ${toDate || '…'}`
  const buildReport = (): ExportReport => ({
    title: 'Evidence',
    subtitle: [orgName, scopeLabel, controlFilter && `Control: ${controlFilter}`].filter(Boolean).join(' · '),
    columns: [
      { key: 'captured', label: 'Captured' },
      { key: 'what', label: 'What happened' },
      { key: 'control', label: 'Control' },
      { key: 'hash', label: 'Hash' },
    ],
    rows: sorted.map((e) => ({
      captured: new Date(e.created_at).toLocaleString(),
      what: e.summary ?? e.evidence_type ?? '—',
      control: e.control_code ? `${e.control_code} — ${e.control_name ?? ''}`.trim() : '—',
      hash: e.evidence_hash ?? '—',
    })),
  })

  return (
    <div>
      <div className="flex items-start justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Evidence</h1>
          <p className="mt-1 text-sm text-ink-soft">
            A hashed evidence record is captured automatically for every test execution — the hash proves the
            summary hasn't been altered after the fact.
          </p>
        </div>
        {!error && !loading && sorted.length > 0 && <ExportButton report={buildReport} />}
      </div>
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
                    {expandedId === e.evidence_id ? 'Hide' : 'View details'}
                  </button>
                </td>
              </tr>
            ))}
            {expandedId &&
              sorted
                .filter((e) => e.evidence_id === expandedId)
                .map((e) => {
                  const rows = humanizeEvidenceLocation(e.evidence_location)
                  return (
                    <tr key={`${e.evidence_id}-detail`}>
                      <td colSpan={4} className="bg-bg px-6 py-3">
                        {rows.length === 0 ? (
                          <p className="text-xs text-ink-soft">No additional detail recorded.</p>
                        ) : (
                          <dl className="grid grid-cols-[max-content,1fr] gap-x-4 gap-y-1 text-xs">
                            {rows.map((r) => (
                              <Fragment key={r.label}>
                                <dt className="text-ink-soft">{r.label}</dt>
                                <dd className="text-ink">{r.value}</dd>
                              </Fragment>
                            ))}
                          </dl>
                        )}
                      </td>
                    </tr>
                  )
                })}
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
