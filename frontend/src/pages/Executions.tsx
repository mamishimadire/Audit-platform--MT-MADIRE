import { Fragment, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { apiClient } from '../lib/apiClient'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import { ExceptionExplanationBlock } from '../components/ExceptionExplanation'
import type { AuditTestOut, ExceptionOut, TestExecutionOut } from '../types/api'

// Mirrors app.core.execution_status on the backend — six statuses instead
// of the old binary completed/failed, so a blocked/unmapped control never
// reads the same as a genuine finding, and a run with nothing to check
// never quietly reads as a clean pass.
const STATUS_STYLES: Record<string, string> = {
  pass: 'bg-accent-soft text-accent-ink',
  exception: 'bg-red-50 text-red-700',
  mapping_required: 'bg-orange-50 text-orange-700',
  not_testable: 'bg-orange-50 text-orange-700',
  insufficient_data: 'bg-bg text-ink-soft',
  error: 'bg-red-50 text-red-700',
  running: 'bg-bg text-ink-soft',
}
const STATUS_LABELS: Record<string, string> = {
  pass: 'Passed',
  exception: 'Exception found',
  mapping_required: 'Mapping needed',
  not_testable: 'Not testable yet',
  insufficient_data: 'No data yet',
  error: 'Technical error',
  running: 'Running',
}
// Plain-English, "explained to a 5 year old" version of each status — shown
// whenever a row doesn't already have its own specific reason text.
const STATUS_EXPLANATIONS: Record<string, string> = {
  mapping_required: "This control has a rule, but not all of the information it needs has been approved and connected yet, so nothing could be checked.",
  not_testable: "This control needs a table that hasn't been found or connected yet, so it can't be checked at all.",
  insufficient_data: "The test ran, but there was no information to check — so there's nothing yet to judge this control by.",
  error: "The test couldn't run because of a technical problem (like a connection dropping), not because of anything wrong with the control.",
}

// A control that couldn't actually be checked — for any of three different
// reasons — is a different question from "did this control pass or fail,"
// so it gets its own combined filter (mirrors app.core.execution_status.
// NEEDS_ATTENTION on the backend, and the Dashboard's own "Needs Attention"
// tile) alongside the granular per-status tabs.
const NEEDS_ATTENTION_STATUSES = new Set(['mapping_required', 'not_testable', 'error'])

const STATUS_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'exception', label: 'Exceptions' },
  { key: 'needs_attention', label: 'Needs attention' },
  { key: 'pass', label: 'Passed' },
  { key: 'mapping_required', label: 'Mapping needed' },
  { key: 'not_testable', label: 'Not testable' },
  { key: 'insufficient_data', label: 'No data' },
  { key: 'error', label: 'Errors' },
] as const
type StatusFilterKey = (typeof STATUS_FILTERS)[number]['key']

type HistoryPreset = 'today' | 'week' | 'month' | 'all' | 'custom'

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

const VALID_FILTER_KEYS = new Set(STATUS_FILTERS.map((f) => f.key))

export function ExecutionsPage() {
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [searchParams] = useSearchParams()
  const [executions, setExecutions] = useState<TestExecutionOut[]>([])
  const [tests, setTests] = useState<AuditTestOut[]>([])
  const [exceptions, setExceptions] = useState<ExceptionOut[]>([])
  // Gated on the executions fetch specifically (the one that determines
  // what the table actually shows) — without this, the table has no way
  // to tell "still loading" apart from "genuinely zero executions," and
  // flashes an empty state on every page load until the fetch resolves.
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  // Arriving from a Dashboard tile link (e.g. "Controls Failing Now" ->
  // /executions?status=exception) pre-selects the matching tab, so the
  // reader lands exactly on what the tile was counting instead of "All"
  // and having to re-find it.
  const initialFilter = searchParams.get('status')
  const [filter, setFilter] = useState<StatusFilterKey>(
    initialFilter && VALID_FILTER_KEYS.has(initialFilter as StatusFilterKey) ? (initialFilter as StatusFilterKey) : 'all'
  )
  const [expandedId, setExpandedId] = useState<string | null>(null)
  // "Current" shows only the most recent run of each test — the answer to
  // "is this control passing right now" without last week's re-runs of the
  // same failure burying it. "History" is every run ever, with a date
  // range to narrow it down — nothing is hidden there, just not the
  // default view, so a demo/test cycle's noise doesn't read as today's status.
  const initialView = searchParams.get('view') === 'history' ? 'history' : 'current'
  const [view, setView] = useState<'current' | 'history'>(initialView)
  // A Dashboard "(all-time)" tile linking into History would otherwise land
  // on just today's date range by default and look empty — "all" is the
  // only preset that actually matches what an all-time count promises.
  const [historyPreset, setHistoryPreset] = useState<HistoryPreset>(initialView === 'history' ? 'all' : 'today')
  const [fromDate, setFromDate] = useState(() => (initialView === 'history' ? '' : isoDate(new Date())))
  const [toDate, setToDate] = useState(() => (initialView === 'history' ? '' : isoDate(new Date())))

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
    // 'custom' leaves whatever the user has typed in the date fields alone.
  }

  useEffect(() => {
    if (!organizationId) return
    setLoading(true)
    setLoadError(false)
    apiClient
      .get<TestExecutionOut[]>(`/organizations/${organizationId}/executions`)
      .then((res) => setExecutions(res.data))
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false))
    apiClient.get<AuditTestOut[]>(`/organizations/${organizationId}/audit-tests`).then((res) => setTests(res.data))
    apiClient.get<ExceptionOut[]>(`/organizations/${organizationId}/exceptions`).then((res) => setExceptions(res.data))
  }, [organizationId])

  const testLabel = (id: string) => {
    const t = tests.find((t) => t.audit_test_id === id)
    return t ? (t.test_code ? `${t.test_code} — ${t.test_name}` : t.test_name) : id
  }

  // A run that completed but found violations has its "reason" as the
  // exceptions it produced (every execution path already creates real
  // Exception rows linked by execution_id). A run that never got to
  // execute at all — a connection failure, for instance — has none of
  // those; its reason is execution_log instead, rendered below. Each
  // ExceptionOut already carries its own plain-English summary/why-it-
  // matters/what-to-do (see execution_service.list_exceptions_for_
  // organization) — no separate fetch needed to show the full picture.
  const exceptionsFor = (executionId: string) => exceptions.filter((e) => e.execution_id === executionId)

  const sorted = [...executions].sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime())

  const latestPerTest = (() => {
    const seen = new Set<string>()
    const out: TestExecutionOut[] = []
    for (const e of sorted) {
      if (seen.has(e.audit_test_id)) continue
      seen.add(e.audit_test_id)
      out.push(e)
    }
    return out.sort((a, b) => testLabel(a.audit_test_id).localeCompare(testLabel(b.audit_test_id)))
  })()

  const base = view === 'current' ? latestPerTest : sorted.filter((e) => {
    const started = new Date(e.started_at)
    if (fromDate && started < new Date(fromDate)) return false
    if (toDate && started > new Date(`${toDate}T23:59:59`)) return false
    return true
  })

  const counts = {
    all: base.length,
    exception: base.filter((e) => e.status === 'exception').length,
    needs_attention: base.filter((e) => NEEDS_ATTENTION_STATUSES.has(e.status)).length,
    pass: base.filter((e) => e.status === 'pass').length,
    mapping_required: base.filter((e) => e.status === 'mapping_required').length,
    not_testable: base.filter((e) => e.status === 'not_testable').length,
    insufficient_data: base.filter((e) => e.status === 'insufficient_data').length,
    error: base.filter((e) => e.status === 'error').length,
  }

  const filtered = base.filter((e) => {
    if (filter === 'all') return true
    if (filter === 'needs_attention') return NEEDS_ATTENTION_STATUSES.has(e.status)
    return e.status === filter
  })

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Executions</h1>
      <p className="mt-1 text-sm text-ink-soft">
        {view === 'current'
          ? "The most recent run of each test — this is the control's status right now."
          : 'Every run of every audit test in this organization — pick a day, a week, a month, or any custom range.'}{' '}
        A real problem is always recorded as an exception, never silently reinterpreted as a pass.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

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
      </div>

      <div className="mt-3 flex gap-1 rounded-md bg-bg p-0.5 text-xs w-fit">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`rounded px-3 py-1.5 font-medium ${filter === f.key ? 'bg-surface text-ink shadow-sm' : 'text-ink-soft'}`}
          >
            {f.label} <span className="text-ink-faint">{counts[f.key]}</span>
          </button>
        ))}
      </div>

      <div className="mt-3 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Audit test</th>
              <th className="px-4 py-2">Date and time</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Records analyzed</th>
              <th className="px-4 py-2">Exceptions</th>
              <th className="px-4 py-2">Details</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((e) => {
              // "Current" (latest run per test) matches by audit_test_id and
              // open status instead of requiring the exact execution_id — a
              // currently-open exception's execution_id moves forward to
              // whichever run most recently re-detected it (see execution_
              // service.record_execution_report), which can be a beat ahead
              // of this page's own, separately-fetched executions list on a
              // fast-cycling schedule. "History" keeps the exact execution_id
              // match, since each row's own reason should reflect what THAT
              // specific run found.
              const rowExceptions =
                e.status !== 'exception'
                  ? []
                  : view === 'current'
                    ? exceptions.filter((x) => x.audit_test_id === e.audit_test_id && x.status !== 'resolved' && x.status !== 'closed')
                    : exceptionsFor(e.execution_id)
              const isExpanded = expandedId === e.execution_id
              const preview = rowExceptions[0]?.summary ?? rowExceptions[0]?.exception_description ?? ''
              return (
                <Fragment key={e.execution_id}>
                  <tr className="border-t border-line align-top">
                    <td className="px-4 py-2 font-medium text-ink">{testLabel(e.audit_test_id)}</td>
                    <td className="px-4 py-2 text-ink-soft whitespace-nowrap">{new Date(e.started_at).toLocaleString()}</td>
                    <td className="px-4 py-2">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[e.status] ?? ''}`}>
                        {STATUS_LABELS[e.status] ?? e.status}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-ink-soft">{e.records_analyzed ?? '—'}</td>
                    <td className="px-4 py-2 text-ink-soft">{e.exceptions_found ?? '—'}</td>
                    <td className="px-4 py-2 text-ink-soft max-w-md">
                      {rowExceptions.length === 0 ? (
                        e.execution_log || STATUS_EXPLANATIONS[e.status] || '—'
                      ) : (
                        <>
                          {preview}
                          {rowExceptions.length > 1 && <span className="text-ink-faint"> (+{rowExceptions.length - 1} more)</span>}
                          <button
                            onClick={() => setExpandedId(isExpanded ? null : e.execution_id)}
                            className="ml-1 font-medium text-accent-ink hover:underline"
                          >
                            {isExpanded ? 'hide full explanation' : 'show full explanation'}
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                  {isExpanded && rowExceptions.length > 0 && (
                    <tr className="border-t border-line-soft bg-bg">
                      <td colSpan={6} className="space-y-2 px-4 py-3">
                        {rowExceptions.map((exc) => (
                          <ExceptionExplanationBlock
                            key={exc.exception_id}
                            summary={exc.summary ?? exc.exception_description ?? 'This record failed the check.'}
                            whyItMatters={exc.why_it_matters ?? 'This check exists to catch a real problem.'}
                            whatToDo={exc.what_to_do ?? 'Look into this record and decide what needs to change.'}
                          />
                        ))}
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
            {loading && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-ink-soft">
                  Loading executions…
                </td>
              </tr>
            )}
            {!loading && loadError && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-red-600">
                  Could not load executions. Try refreshing the page.
                </td>
              </tr>
            )}
            {!loading && !loadError && filtered.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-ink-soft">
                  No test executions{filter !== 'all' ? ` with status "${filter}"` : ''} yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
