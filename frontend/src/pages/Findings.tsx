import { Fragment, useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { AUDIT_FRAMEWORK_ROLES } from '../auth/permissions'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import { ExceptionExplanationBlock } from '../components/ExceptionExplanation'
import type { ExceptionExplanationOut, ExceptionOut, FindingOut, RemediationActionOut, RetestOut, RootCauseOut, TraceNode } from '../types/api'

function inNDays(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

const STATUS_STYLES: Record<string, string> = {
  open: 'bg-red-50 text-red-700',
  remediation_in_progress: 'bg-orange-50 text-orange-700',
  awaiting_retest: 'bg-bg text-ink-soft',
  closed: 'bg-accent-soft text-accent-ink',
  reopened: 'bg-red-50 text-red-700',
}

const RISK_STYLES: Record<string, string> = {
  critical: 'bg-red-50 text-red-700',
  high: 'bg-orange-50 text-orange-700',
  medium: 'bg-bg text-ink-soft',
  low: 'bg-accent-soft text-accent-ink',
}

function FindingDetail({ finding, onChanged }: { finding: FindingOut; onChanged: () => void }) {
  const { hasRole } = useAuth()
  const canManage = hasRole(...AUDIT_FRAMEWORK_ROLES)
  const [trace, setTrace] = useState<TraceNode[]>([])
  const [rootCauses, setRootCauses] = useState<RootCauseOut[]>([])
  const [actions, setActions] = useState<RemediationActionOut[]>([])
  const [retests, setRetests] = useState<RetestOut[]>([])
  const [explanation, setExplanation] = useState<ExceptionExplanationOut | null>(null)

  const [rcCategory, setRcCategory] = useState('')
  const [rcDescription, setRcDescription] = useState('')
  const [actionDescription, setActionDescription] = useState('')
  const [targetDate, setTargetDate] = useState('')
  const [retestComments, setRetestComments] = useState('')

  const load = () => {
    apiClient.get<{ chain: TraceNode[] }>(`/findings/${finding.finding_id}/trace`).then((res) => setTrace(res.data.chain))
    apiClient.get<RootCauseOut[]>(`/findings/${finding.finding_id}/root-causes`).then((res) => setRootCauses(res.data))
    apiClient.get<RemediationActionOut[]>(`/findings/${finding.finding_id}/remediation-actions`).then((res) => setActions(res.data))
    apiClient.get<RetestOut[]>(`/findings/${finding.finding_id}/retests`).then((res) => setRetests(res.data))
    apiClient
      .get<ExceptionExplanationOut>(`/exceptions/${finding.exception_id}/explanation`)
      .then((res) => setExplanation(res.data))
  }

  useEffect(load, [finding.finding_id])

  // Suggest a starting point for root cause and remediation instead of a
  // blank form — the exception behind this finding already carries a
  // description and (for endpoint/software checks) ready-written
  // remediation guidance. Both fields stay in normal editable inputs, so
  // an auditor reviews/edits before adding, never submits blind. Runs once
  // per finding (not tied to `load`, which also re-fires on every add —
  // re-prefilling there would overwrite whatever the auditor just typed).
  useEffect(() => {
    if (!canManage) return
    apiClient.get<ExceptionOut>(`/exceptions/${finding.exception_id}`).then((res) => {
      const exception = res.data
      setRcDescription((prev) => prev || exception.exception_description || '')
      setActionDescription((prev) => prev || exception.recommended_remediation || '')
      setTargetDate((prev) => prev || inNDays(30))
    })
  }, [finding.finding_id, canManage])

  const addRootCause = async () => {
    await apiClient.post(`/findings/${finding.finding_id}/root-causes`, { root_cause_category: rcCategory || null, description: rcDescription || null })
    setRcCategory('')
    setRcDescription('')
    load()
  }

  const addAction = async () => {
    await apiClient.post(`/findings/${finding.finding_id}/remediation-actions`, { action_description: actionDescription, target_date: targetDate || null })
    setActionDescription('')
    setTargetDate('')
    load()
    onChanged()
  }

  const completeAction = async (id: string) => {
    await apiClient.patch(`/remediation-actions/${id}`, { status: 'completed' })
    load()
    onChanged()
  }

  const doRetest = async (result: 'pass' | 'fail') => {
    await apiClient.post(`/findings/${finding.finding_id}/retests`, {
      audit_test_id: trace.find((n) => n.level === 'audit_test')?.id,
      result,
      comments: retestComments || null,
    })
    setRetestComments('')
    load()
    onChanged()
  }

  return (
    <div className="space-y-4 bg-bg p-4">
      {explanation && (
        <ExceptionExplanationBlock
          summary={explanation.summary}
          whyItMatters={explanation.why_it_matters}
          whatToDo={explanation.what_to_do}
        />
      )}

      <div className="flex flex-wrap items-center gap-1 text-xs text-ink-soft">
        {trace.map((n, i) => (
          <span key={n.id}>
            <span className="rounded-full bg-surface px-2 py-0.5 font-mono">{n.level}</span>
            <span className="mx-1 font-medium text-ink">{n.label}</span>
            {i < trace.length - 1 && <span className="mx-1">→</span>}
          </span>
        ))}
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <div className="rounded-lg border border-line bg-surface p-3">
          <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Root causes</div>
          <ul className="mt-2 space-y-1">
            {rootCauses.map((rc) => (
              <li key={rc.root_cause_id} className="text-xs">
                <span className="font-medium">{rc.root_cause_category}</span> — {rc.description}
              </li>
            ))}
          </ul>
          {canManage && (
            <div className="mt-2 space-y-1">
              <input placeholder="Category" value={rcCategory} onChange={(e) => setRcCategory(e.target.value)} className="w-full rounded-md border border-line px-2 py-1 text-xs" />
              <input placeholder="Description" value={rcDescription} onChange={(e) => setRcDescription(e.target.value)} className="w-full rounded-md border border-line px-2 py-1 text-xs" />
              <button onClick={addRootCause} className="rounded-md bg-accent px-2 py-1 text-xs font-medium text-white">
                Add root cause
              </button>
            </div>
          )}
        </div>

        <div className="rounded-lg border border-line bg-surface p-3">
          <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Remediation actions</div>
          <ul className="mt-2 space-y-1">
            {actions.map((a) => (
              <li key={a.remediation_id} className="text-xs">
                <div className="flex items-center justify-between">
                  <span>{a.action_description}</span>
                  {canManage && a.status !== 'completed' && (
                    <button onClick={() => completeAction(a.remediation_id)} className="text-accent-ink hover:underline">
                      Mark complete
                    </button>
                  )}
                </div>
                <span className={`text-ink-soft ${a.is_overdue ? 'text-red-600' : ''}`}>
                  {a.status} {a.target_date ? `· due ${a.target_date}` : ''} {a.is_overdue ? '· OVERDUE' : ''}
                </span>
              </li>
            ))}
          </ul>
          {canManage && (finding.status === 'open' || finding.status === 'remediation_in_progress' || finding.status === 'reopened') && (
            <div className="mt-2 space-y-1">
              <input placeholder="Action description" value={actionDescription} onChange={(e) => setActionDescription(e.target.value)} className="w-full rounded-md border border-line px-2 py-1 text-xs" />
              <input type="date" value={targetDate} onChange={(e) => setTargetDate(e.target.value)} className="w-full rounded-md border border-line px-2 py-1 text-xs" />
              <button onClick={addAction} className="rounded-md bg-accent px-2 py-1 text-xs font-medium text-white">
                Add remediation action
              </button>
            </div>
          )}
        </div>

        <div className="rounded-lg border border-line bg-surface p-3">
          <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Re-tests</div>
          <ul className="mt-2 space-y-1">
            {retests.map((r) => (
              <li key={r.retest_id} className="text-xs">
                <span className={r.result === 'pass' ? 'text-accent-ink' : 'text-red-600'}>{r.result?.toUpperCase()}</span>{' '}
                {new Date(r.retest_date).toLocaleDateString()} — {r.comments}
              </li>
            ))}
          </ul>
          {canManage && finding.status === 'awaiting_retest' ? (
            <div className="mt-2 space-y-1">
              <input placeholder="Comments" value={retestComments} onChange={(e) => setRetestComments(e.target.value)} className="w-full rounded-md border border-line px-2 py-1 text-xs" />
              <div className="flex gap-1">
                <button onClick={() => doRetest('pass')} className="flex-1 rounded-md bg-accent px-2 py-1 text-xs font-medium text-white">
                  PASS → Close
                </button>
                <button onClick={() => doRetest('fail')} className="flex-1 rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white">
                  FAIL → Reopen
                </button>
              </div>
            </div>
          ) : (
            <p className="mt-2 text-xs text-ink-soft">
              Re-test becomes available once a remediation action is marked complete — never auto-closed.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

const FINDING_STATUS_OPTIONS = ['open', 'remediation_in_progress', 'awaiting_retest', 'closed', 'reopened']

export function FindingsPage() {
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [findings, setFindings] = useState<FindingOut[]>([])
  const [expandedId, setExpandedId] = useState<string | null>(null)
  // "Active" hides closed findings; "History" reveals every status plus a
  // date range over when the finding was identified — same pattern as
  // Exceptions, nothing here is ever deleted either.
  const [view, setView] = useState<'active' | 'history'>('active')
  const [statusFilter, setStatusFilter] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  // Starts true — otherwise the table has no way to tell "still loading"
  // apart from "genuinely zero findings," and flashes an empty state on
  // every page load until the fetch resolves.
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)

  const load = (orgId: string) => {
    setLoading(true)
    setLoadError(false)
    apiClient
      .get<FindingOut[]>(`/organizations/${orgId}/findings`)
      .then((res) => setFindings(res.data))
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (organizationId) load(organizationId)
  }, [organizationId])

  const visibleFindings = findings.filter((f) => {
    if (view === 'active') return f.status !== 'closed'
    if (statusFilter && f.status !== statusFilter) return false
    const identified = new Date(f.identified_at)
    if (fromDate && identified < new Date(fromDate)) return false
    if (toDate && identified > new Date(`${toDate}T23:59:59`)) return false
    return true
  })

  // Grouped by control, same reasoning as the Exceptions page — every
  // finding for ONE control together, not one flat undifferentiated list.
  const findingGroups = (() => {
    const byLabel = new Map<string, { label: string; code: string; items: FindingOut[] }>()
    for (const f of visibleFindings) {
      const label = f.control_code ? `${f.control_code} — ${f.control_name}` : 'Uncategorized'
      const key = f.control_code ?? '￿'
      if (!byLabel.has(key)) byLabel.set(key, { label, code: key, items: [] })
      byLabel.get(key)!.items.push(f)
    }
    return [...byLabel.values()].sort((a, b) => a.code.localeCompare(b.code))
  })()

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Findings</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Auditor-confirmed exceptions. Escalate an exception to a finding from the Exceptions page.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <div className="flex gap-1 rounded-md bg-bg p-0.5 text-xs">
          <button
            onClick={() => setView('active')}
            className={`rounded px-3 py-1.5 font-medium ${view === 'active' ? 'bg-surface text-ink shadow-sm' : 'text-ink-soft'}`}
          >
            Active
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
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="rounded-md border border-line px-2 py-1 text-xs">
              <option value="">All statuses</option>
              {FINDING_STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <label className="flex items-center gap-1 text-xs text-ink-soft">
              From
              <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} className="rounded-md border border-line px-2 py-1 text-xs" />
            </label>
            <label className="flex items-center gap-1 text-xs text-ink-soft">
              To
              <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} className="rounded-md border border-line px-2 py-1 text-xs" />
            </label>
            {(statusFilter || fromDate || toDate) && (
              <button
                onClick={() => {
                  setStatusFilter('')
                  setFromDate('')
                  setToDate('')
                }}
                className="text-xs font-medium text-accent-ink hover:underline"
              >
                Clear filters
              </button>
            )}
          </>
        )}
        <span className="text-xs text-ink-soft">{visibleFindings.length} shown</span>
      </div>

      <div className="mt-4 space-y-px overflow-hidden rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line bg-surface text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Title</th>
              <th className="px-4 py-2">Risk</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Identified</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {findingGroups.map((group) => (
              <Fragment key={group.code}>
                <tr className="border-t border-line bg-bg">
                  <td colSpan={5} className="px-4 py-1.5 text-xs font-semibold uppercase tracking-wide text-ink-soft">
                    {group.label} <span className="font-normal text-ink-faint">({group.items.length})</span>
                  </td>
                </tr>
                {group.items.map((f) => (
                  <Fragment key={f.finding_id}>
                    <tr className="border-t border-line bg-surface">
                      <td className="px-4 py-2 font-medium text-ink">{f.finding_title}</td>
                      <td className="px-4 py-2">
                        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${RISK_STYLES[f.risk_rating ?? ''] ?? ''}`}>{f.risk_rating ?? '—'}</span>
                      </td>
                      <td className="px-4 py-2">
                        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[f.status] ?? ''}`}>{f.status}</span>
                      </td>
                      <td className="px-4 py-2 text-xs text-ink-soft">{new Date(f.identified_at).toLocaleDateString()}</td>
                      <td className="px-4 py-2">
                        <button onClick={() => setExpandedId(expandedId === f.finding_id ? null : f.finding_id)} className="text-xs font-medium text-accent-ink hover:underline">
                          {expandedId === f.finding_id ? 'Hide' : 'Open'}
                        </button>
                      </td>
                    </tr>
                    {expandedId === f.finding_id && (
                      <tr>
                        <td colSpan={5} className="p-0">
                          <FindingDetail finding={f} onChanged={() => organizationId && load(organizationId)} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </Fragment>
            ))}
            {loading && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-ink-soft">
                  Loading findings…
                </td>
              </tr>
            )}
            {!loading && loadError && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-red-600">
                  Could not load findings. Try refreshing the page.
                </td>
              </tr>
            )}
            {!loading && !loadError && visibleFindings.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-ink-soft">
                  {findings.length === 0 ? 'No findings yet.' : 'No findings match these filters.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
