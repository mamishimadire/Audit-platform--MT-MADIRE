import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import { ExportButton } from '../components/ExportButton'
import type { ExportReport } from '../lib/exportTable'
import type { AuditLogOut } from '../types/api'

type HistoryPreset = 'today' | 'week' | 'month' | 'all' | 'custom'

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

export function AuditTrailPage() {
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [logs, setLogs] = useState<AuditLogOut[] | null>(null)
  const [error, setError] = useState(false)
  const [loading, setLoading] = useState(true)

  // "Current" is today's activity — the answer to "what's happening right
  // now." "History" is everything else, with a date range to narrow it
  // down, same shape as the Executions page.
  const [view, setView] = useState<'current' | 'history'>('current')
  const [historyPreset, setHistoryPreset] = useState<HistoryPreset>('today')
  const [fromDate, setFromDate] = useState(() => isoDate(new Date()))
  const [toDate, setToDate] = useState(() => isoDate(new Date()))
  const [entityFilter, setEntityFilter] = useState('')
  const [search, setSearch] = useState('')

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
    const params: Record<string, string> = { limit: '2000' }
    const effectiveFrom = view === 'current' ? isoDate(new Date()) : fromDate
    const effectiveTo = view === 'current' ? isoDate(new Date()) : toDate
    if (effectiveFrom) params.from_date = effectiveFrom
    if (effectiveTo) params.to_date = effectiveTo
    apiClient
      .get<AuditLogOut[]>(`/organizations/${organizationId}/audit-logs`, { params })
      .then((res) => setLogs(res.data))
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [organizationId, view, fromDate, toDate])

  const entityTypes = [...new Set((logs ?? []).map((l) => l.entity_type).filter((t): t is string => !!t))].sort()

  const visible = (logs ?? []).filter((l) => {
    if (entityFilter && l.entity_type !== entityFilter) return false
    if (search && !l.action.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  const orgName = organizations.find((o) => o.organization_id === organizationId)?.organization_name ?? ''
  const scopeLabel =
    view === 'current'
      ? 'Today'
      : historyPreset === 'all'
        ? 'All time'
        : `${fromDate || '…'} to ${toDate || '…'}`
  const buildReport = (): ExportReport => ({
    title: 'Audit Trail',
    subtitle: [orgName, scopeLabel, entityFilter && `Entity: ${entityFilter}`, search && `Search: “${search}”`]
      .filter(Boolean)
      .join(' · '),
    columns: [
      { key: 'when', label: 'When' },
      { key: 'who', label: 'Who' },
      { key: 'action', label: 'Action' },
      { key: 'entity', label: 'Entity' },
      { key: 'change', label: 'Change' },
    ],
    rows: visible.map((l) => ({
      when: new Date(l.timestamp).toLocaleString(),
      who: l.user_name ?? 'System',
      action: l.action,
      entity: l.entity_type ?? '—',
      change: l.change_summary ?? '—',
    })),
  })

  return (
    <div>
      <div className="flex items-start justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Audit Trail</h1>
          <p className="mt-1 text-sm text-ink-soft">
            Every logged platform action for this organization — who, what, when. Protected from modification; this
            is a read-only view.
          </p>
        </div>
        {!error && !loading && visible.length > 0 && <ExportButton report={buildReport} />}
      </div>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      {error && <p className="mt-4 text-sm text-red-600">You don't have permission to view this organization's audit trail.</p>}

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
        <select value={entityFilter} onChange={(e) => setEntityFilter(e.target.value)} className="rounded-md border border-line px-2 py-1 text-xs">
          <option value="">All entity types</option>
          {entityTypes.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <input
          placeholder="Search actions…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="rounded-md border border-line px-2 py-1 text-xs"
        />
      </div>

      <div className="mt-3 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">When</th>
              <th className="px-4 py-2">Who</th>
              <th className="px-4 py-2">Action</th>
              <th className="px-4 py-2">Entity</th>
              <th className="px-4 py-2">Change</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((log) => (
              <tr key={log.log_id} className="border-t border-line align-top">
                <td className="whitespace-nowrap px-4 py-2 text-xs text-ink-soft">{new Date(log.timestamp).toLocaleString()}</td>
                <td className="whitespace-nowrap px-4 py-2 text-xs text-ink-soft">{log.user_name ?? 'System'}</td>
                <td className="px-4 py-2 text-sm text-ink">{log.action}</td>
                <td className="px-4 py-2 font-mono text-xs text-ink-soft">{log.entity_type ?? '—'}</td>
                <td className="px-4 py-2 text-xs text-ink-soft">{log.change_summary ?? '—'}</td>
              </tr>
            ))}
            {loading && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-ink-soft">
                  Loading activity…
                </td>
              </tr>
            )}
            {!loading && !error && visible.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-ink-soft">
                  No activity logged {view === 'current' ? 'today' : 'in this range'}.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
