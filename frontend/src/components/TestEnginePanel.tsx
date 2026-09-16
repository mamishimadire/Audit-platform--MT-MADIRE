import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { AUDIT_FRAMEWORK_ROLES } from '../auth/permissions'
import { RulePreview } from './RulePreview'
import type { MonitoringScheduleOut, TestExecutionOut, TestRuleOut } from '../types/api'

const OPERATORS = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'is_null', 'is_not_null']

const ORIGIN_LABELS: Record<string, string> = {
  manual: 'Manual',
  auto_generated: 'Auto-generated from control template',
  auto_generated_edited: 'Auto-generated, edited',
}
const ORIGIN_STYLES: Record<string, string> = {
  manual: 'bg-bg text-ink-soft',
  auto_generated: 'bg-accent-soft text-accent-ink',
  auto_generated_edited: 'bg-blue-50 text-blue-700',
}

// Mirrors app.core.execution_status on the backend (and Executions.tsx's
// own copy of this map) — see that module's docstring for what each status
// means.
const EXECUTION_STYLES: Record<string, string> = {
  pass: 'bg-accent-soft text-accent-ink',
  exception: 'bg-red-50 text-red-700',
  mapping_required: 'bg-orange-50 text-orange-700',
  not_testable: 'bg-orange-50 text-orange-700',
  insufficient_data: 'bg-bg text-ink-soft',
  error: 'bg-red-50 text-red-700',
  running: 'bg-bg text-ink-soft',
}
const EXECUTION_LABELS: Record<string, string> = {
  pass: 'Passed',
  exception: 'Exception found',
  mapping_required: 'Mapping needed',
  not_testable: 'Not testable yet',
  insufficient_data: 'No data yet',
  error: 'Technical error',
  running: 'Running',
}

type RuleType = 'threshold' | 'duplicate' | 'missing_match' | 'cross_match_condition'

interface Props {
  organizationId: string
  auditTestId: string
}

export function TestEnginePanel({ organizationId, auditTestId }: Props) {
  const { hasRole } = useAuth()
  const canManage = hasRole(...AUDIT_FRAMEWORK_ROLES)
  const [rules, setRules] = useState<TestRuleOut[]>([])
  const [schedules, setSchedules] = useState<MonitoringScheduleOut[]>([])
  const [executions, setExecutions] = useState<TestExecutionOut[]>([])
  const [error, setError] = useState<string | null>(null)
  const [canonicalObjects, setCanonicalObjects] = useState<string[]>(['employee', 'user'])

  useEffect(() => {
    apiClient.get<string[]>('/canonical-model/objects').then((res) => setCanonicalObjects(res.data))
  }, [])

  const [showRuleForm, setShowRuleForm] = useState(false)
  const [editingRuleId, setEditingRuleId] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)
  const [deletingRuleId, setDeletingRuleId] = useState<string | null>(null)
  const [deleteReason, setDeleteReason] = useState('')
  const [approvingRuleId, setApprovingRuleId] = useState<string | null>(null)
  const [approveError, setApproveError] = useState<string | null>(null)
  const [rejectingRuleId, setRejectingRuleId] = useState<string | null>(null)
  const [rejectReason, setRejectReason] = useState('')

  const [ruleName, setRuleName] = useState('')
  const [ruleType, setRuleType] = useState<RuleType>('cross_match_condition')
  const [f, setF] = useState({
    object: 'transaction',
    field: 'amount',
    operator: 'gt',
    value: '',
    groupBy: '',
    primaryObject: 'employee',
    secondaryObject: 'user',
    joinField: 'employee_id',
    condPrimaryField: 'employment_status',
    condPrimaryOp: 'eq',
    condPrimaryValue: 'terminated',
    condSecondaryField: 'status',
    condSecondaryOp: 'eq',
    condSecondaryValue: 'active',
  })
  const [frequency, setFrequency] = useState('daily')
  const [schedulingSubmitting, setSchedulingSubmitting] = useState(false)

  const load = () => {
    apiClient.get<TestRuleOut[]>(`/organizations/${organizationId}/audit-tests/${auditTestId}/test-rules`).then((res) => setRules(res.data))
    apiClient
      .get<MonitoringScheduleOut[]>(`/organizations/${organizationId}/audit-tests/${auditTestId}/schedules`)
      .then((res) => setSchedules(res.data))
    apiClient
      .get<TestExecutionOut[]>(`/organizations/${organizationId}/audit-tests/${auditTestId}/executions`)
      .then((res) => setExecutions(res.data))
  }

  useEffect(load, [organizationId, auditTestId])

  const buildDefinition = (): Record<string, unknown> => {
    if (ruleType === 'threshold') {
      return { rule_type: 'threshold', object: f.object, field: f.field, operator: f.operator, value: isNaN(Number(f.value)) ? f.value : Number(f.value) }
    }
    if (ruleType === 'duplicate') {
      return { rule_type: 'duplicate', object: f.object, group_by: f.groupBy.split(',').map((s) => s.trim()).filter(Boolean) }
    }
    if (ruleType === 'missing_match') {
      return { rule_type: 'missing_match', primary_object: f.primaryObject, secondary_object: f.secondaryObject, join_field: f.joinField }
    }
    return {
      rule_type: 'cross_match_condition',
      primary_object: f.primaryObject,
      secondary_object: f.secondaryObject,
      join_field: f.joinField,
      condition_primary: { field: f.condPrimaryField, operator: f.condPrimaryOp, value: f.condPrimaryValue },
      condition_secondary: { field: f.condSecondaryField, operator: f.condSecondaryOp, value: f.condSecondaryValue },
    }
  }

  const saveRule = async () => {
    setError(null)
    try {
      if (editingRuleId) {
        await apiClient.patch(`/test-rules/${editingRuleId}`, { rule_name: ruleName, rule_definition: buildDefinition() })
      } else {
        await apiClient.post(`/organizations/${organizationId}/audit-tests/${auditTestId}/test-rules`, {
          rule_name: ruleName,
          rule_definition: buildDefinition(),
        })
      }
      setRuleName('')
      setEditingRuleId(null)
      setShowRuleForm(false)
      load()
    } catch {
      setError('Could not save the rule — check the field values.')
    }
  }

  const startEdit = (rule: TestRuleOut) => {
    setEditingRuleId(rule.rule_id)
    setRuleName(rule.rule_name)
    setRuleType((rule.rule_type as RuleType) ?? 'cross_match_condition')
    const d = rule.rule_definition as any
    setF({
      object: d.object ?? 'transaction',
      field: d.field ?? 'amount',
      operator: d.operator ?? 'gt',
      value: d.value !== undefined ? String(d.value) : '',
      groupBy: (d.group_by ?? []).join(', '),
      primaryObject: d.primary_object ?? 'employee',
      secondaryObject: d.secondary_object ?? 'user',
      joinField: d.join_field ?? 'employee_id',
      condPrimaryField: d.condition_primary?.field ?? 'employment_status',
      condPrimaryOp: d.condition_primary?.operator ?? 'eq',
      condPrimaryValue: d.condition_primary?.value ?? 'terminated',
      condSecondaryField: d.condition_secondary?.field ?? 'status',
      condSecondaryOp: d.condition_secondary?.operator ?? 'eq',
      condSecondaryValue: d.condition_secondary?.value ?? 'active',
    })
    setShowRuleForm(true)
  }

  const generateFromTemplate = async () => {
    setGenerating(true)
    setGenerateError(null)
    try {
      await apiClient.post(`/organizations/${organizationId}/audit-tests/${auditTestId}/test-rules/generate-from-template`)
      load()
    } catch (err: any) {
      setGenerateError(err?.response?.data?.detail ?? 'Could not generate a rule from this control template.')
    } finally {
      setGenerating(false)
    }
  }

  const confirmDelete = async () => {
    if (!deletingRuleId || !deleteReason.trim()) return
    await apiClient.delete(`/test-rules/${deletingRuleId}`, { data: { reason: deleteReason } })
    setDeletingRuleId(null)
    setDeleteReason('')
    load()
  }

  const approveRule = async (ruleId: string) => {
    setApprovingRuleId(ruleId)
    setApproveError(null)
    try {
      await apiClient.post(`/test-rules/${ruleId}/approve`)
      load()
    } catch (err: any) {
      setApproveError(err?.response?.data?.detail ?? 'Could not approve this rule.')
    } finally {
      setApprovingRuleId(null)
    }
  }

  const confirmReject = async () => {
    if (!rejectingRuleId || !rejectReason.trim()) return
    await apiClient.post(`/test-rules/${rejectingRuleId}/reject`, { reason: rejectReason })
    setRejectingRuleId(null)
    setRejectReason('')
    load()
  }

  const createSchedule = async () => {
    if (schedulingSubmitting) return
    setSchedulingSubmitting(true)
    try {
      await apiClient.post(`/organizations/${organizationId}/audit-tests/${auditTestId}/schedules`, { frequency, is_active: true })
      await load()
    } finally {
      setSchedulingSubmitting(false)
    }
  }

  const visibleRules = rules.filter((r) => r.status !== 'deleted')
  // A test only ever needs one live schedule; anything else is a
  // superseded duplicate (see backend migration 0045) kept only for its
  // audit trail — listing it here would just be clutter.
  const activeSchedules = schedules.filter((s) => s.is_active)
  const supersededScheduleCount = schedules.length - activeSchedules.length
  const STATUS_STYLES: Record<string, string> = {
    pending_approval: 'bg-orange-100 text-orange-800 font-bold',
    active: 'bg-accent-soft text-accent-ink',
    rejected: 'bg-red-50 text-red-700',
  }
  const STATUS_LABELS: Record<string, string> = {
    pending_approval: 'Pending approval',
    active: 'Active',
    rejected: 'Rejected',
  }

  const objectSelect = (value: string, onChange: (v: string) => void) => (
    <select value={value} onChange={(e) => onChange(e.target.value)} className="rounded-md border border-line px-2 py-1 text-xs">
      {canonicalObjects.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  )

  return (
    <div className="space-y-4 bg-bg p-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-lg border border-line bg-surface p-3">
          <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Test rule</div>
          {visibleRules.length > 0 && (
            <ul className="mt-2 space-y-2">
              {visibleRules.map((r) => (
                <li
                  key={r.rule_id}
                  className={`rounded-md border px-2 py-1.5 ${
                    r.status === 'pending_approval' ? 'border-orange-300 bg-orange-50/60' : 'border-line'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs">
                      <span className={r.status === 'pending_approval' ? 'font-bold text-ink' : 'font-medium text-ink'}>{r.rule_name}</span>{' '}
                      <span className="font-mono text-ink-soft">({r.rule_type})</span>
                    </span>
                    {canManage && (
                      <div className="flex items-center gap-2">
                        {r.status === 'pending_approval' && (
                          <>
                            <button
                              onClick={() => approveRule(r.rule_id)}
                              disabled={approvingRuleId === r.rule_id}
                              className="text-xs font-medium text-accent-ink hover:underline disabled:opacity-60"
                            >
                              {approvingRuleId === r.rule_id ? 'Approving…' : 'Approve'}
                            </button>
                            <button onClick={() => setRejectingRuleId(r.rule_id)} className="text-xs font-medium text-red-600 hover:underline">
                              Reject
                            </button>
                          </>
                        )}
                        <button onClick={() => startEdit(r)} className="text-xs font-medium text-ink-soft hover:underline">
                          Edit
                        </button>
                        <button onClick={() => setDeletingRuleId(r.rule_id)} className="text-xs font-medium text-red-600 hover:underline">
                          Delete
                        </button>
                      </div>
                    )}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${STATUS_STYLES[r.status] ?? ''}`}>
                      {STATUS_LABELS[r.status] ?? r.status}
                    </span>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${ORIGIN_STYLES[r.origin] ?? ''}`}>
                      {ORIGIN_LABELS[r.origin] ?? r.origin}
                    </span>
                    {r.needs_review && (
                      <span className="rounded-full bg-orange-50 px-2 py-0.5 text-[10px] font-medium text-orange-700">
                        ⚠ Needs review — mapping changed
                      </span>
                    )}
                    {r.status === 'rejected' && r.rejected_reason && (
                      <span className="text-[10px] text-ink-soft">— {r.rejected_reason}</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {approveError && <p className="mt-1 text-xs text-red-600">{approveError}</p>}

          <RulePreview organizationId={organizationId} auditTestId={auditTestId} />

          {canManage && !showRuleForm && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                onClick={generateFromTemplate}
                disabled={generating}
                className="rounded-md border border-line px-3 py-1 text-xs font-medium text-ink hover:bg-bg disabled:opacity-60"
              >
                {generating ? 'Generating…' : 'Generate from control template'}
              </button>
              <button
                onClick={() => {
                  setEditingRuleId(null)
                  setRuleName('')
                  setShowRuleForm(true)
                }}
                className="text-xs font-medium text-accent-ink hover:underline"
              >
                + Add rule manually
              </button>
            </div>
          )}
          {generateError && <p className="mt-1 text-xs text-red-600">{generateError}</p>}
          {visibleRules.length === 0 && !showRuleForm && (
            <p className="mt-2 text-xs text-ink-soft">No active rule yet.</p>
          )}

          {showRuleForm && (
            <div className="mt-3 space-y-2">
              <input
                placeholder="Rule name"
                value={ruleName}
                onChange={(e) => setRuleName(e.target.value)}
                className="w-full rounded-md border border-line px-2 py-1 text-xs"
              />
              <select value={ruleType} onChange={(e) => setRuleType(e.target.value as RuleType)} className="w-full rounded-md border border-line px-2 py-1 text-xs">
                <option value="cross_match_condition">Cross-match condition (e.g. terminated + still active)</option>
                <option value="threshold">Threshold (field vs. value)</option>
                <option value="duplicate">Duplicate (group by fields)</option>
                <option value="missing_match">Missing match (anti-join)</option>
              </select>

              {ruleType === 'threshold' && (
                <div className="flex flex-wrap gap-1">
                  {objectSelect(f.object, (v) => setF({ ...f, object: v }))}
                  <input placeholder="field" value={f.field} onChange={(e) => setF({ ...f, field: e.target.value })} className="w-24 rounded-md border border-line px-2 py-1 text-xs" />
                  <select value={f.operator} onChange={(e) => setF({ ...f, operator: e.target.value })} className="rounded-md border border-line px-2 py-1 text-xs">
                    {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                  <input placeholder="value" value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })} className="w-20 rounded-md border border-line px-2 py-1 text-xs" />
                </div>
              )}
              {ruleType === 'duplicate' && (
                <div className="flex flex-wrap gap-1">
                  {objectSelect(f.object, (v) => setF({ ...f, object: v }))}
                  <input placeholder="group_by fields, comma separated" value={f.groupBy} onChange={(e) => setF({ ...f, groupBy: e.target.value })} className="flex-1 rounded-md border border-line px-2 py-1 text-xs" />
                </div>
              )}
              {ruleType === 'missing_match' && (
                <div className="flex flex-wrap items-center gap-1">
                  {objectSelect(f.primaryObject, (v) => setF({ ...f, primaryObject: v }))}
                  <span className="text-xs text-ink-soft">without match in</span>
                  {objectSelect(f.secondaryObject, (v) => setF({ ...f, secondaryObject: v }))}
                  <span className="text-xs text-ink-soft">on</span>
                  <input placeholder="join_field" value={f.joinField} onChange={(e) => setF({ ...f, joinField: e.target.value })} className="w-28 rounded-md border border-line px-2 py-1 text-xs" />
                </div>
              )}
              {ruleType === 'cross_match_condition' && (
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-1">
                    {objectSelect(f.primaryObject, (v) => setF({ ...f, primaryObject: v }))}
                    <span className="text-xs text-ink-soft">×</span>
                    {objectSelect(f.secondaryObject, (v) => setF({ ...f, secondaryObject: v }))}
                    <span className="text-xs text-ink-soft">join on</span>
                    <input placeholder="join_field" value={f.joinField} onChange={(e) => setF({ ...f, joinField: e.target.value })} className="w-28 rounded-md border border-line px-2 py-1 text-xs" />
                  </div>
                  <div className="flex flex-wrap gap-1">
                    <input placeholder="primary field" value={f.condPrimaryField} onChange={(e) => setF({ ...f, condPrimaryField: e.target.value })} className="w-28 rounded-md border border-line px-2 py-1 text-xs" />
                    <select value={f.condPrimaryOp} onChange={(e) => setF({ ...f, condPrimaryOp: e.target.value })} className="rounded-md border border-line px-2 py-1 text-xs">
                      {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                    <input placeholder="value" value={f.condPrimaryValue} onChange={(e) => setF({ ...f, condPrimaryValue: e.target.value })} className="w-20 rounded-md border border-line px-2 py-1 text-xs" />
                  </div>
                  <div className="flex flex-wrap gap-1">
                    <input placeholder="secondary field" value={f.condSecondaryField} onChange={(e) => setF({ ...f, condSecondaryField: e.target.value })} className="w-28 rounded-md border border-line px-2 py-1 text-xs" />
                    <select value={f.condSecondaryOp} onChange={(e) => setF({ ...f, condSecondaryOp: e.target.value })} className="rounded-md border border-line px-2 py-1 text-xs">
                      {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                    <input placeholder="value" value={f.condSecondaryValue} onChange={(e) => setF({ ...f, condSecondaryValue: e.target.value })} className="w-20 rounded-md border border-line px-2 py-1 text-xs" />
                  </div>
                </div>
              )}
              {error && <p className="text-xs text-red-600">{error}</p>}
              <div className="flex gap-2">
                <button onClick={saveRule} className="rounded-md bg-accent px-3 py-1 text-xs font-medium text-white">
                  {editingRuleId ? 'Save changes' : 'Create rule'}
                </button>
                <button
                  onClick={() => {
                    setShowRuleForm(false)
                    setEditingRuleId(null)
                  }}
                  className="rounded-md border border-line px-3 py-1 text-xs font-medium text-ink-soft hover:bg-bg"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="rounded-lg border border-line bg-surface p-3">
          <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Monitoring schedule</div>
          {activeSchedules.length > 0 ? (
            <>
              <ul className="mt-2 space-y-1">
                {activeSchedules.map((s) => (
                  <li key={s.schedule_id} className="text-xs">
                    {s.frequency} · active · next run{' '}
                    {s.next_run ? new Date(s.next_run).toLocaleString() : '—'}
                    {s.last_run && <> · last run {new Date(s.last_run).toLocaleString()}</>}
                  </li>
                ))}
              </ul>
              {supersededScheduleCount > 0 && (
                <p className="mt-1 text-[10px] text-ink-soft">
                  {supersededScheduleCount} superseded schedule{supersededScheduleCount === 1 ? '' : 's'} hidden (kept for history only).
                </p>
              )}
            </>
          ) : canManage ? (
            <div className="mt-3 flex gap-2">
              <select value={frequency} onChange={(e) => setFrequency(e.target.value)} className="rounded-md border border-line px-2 py-1 text-xs">
                <option value="real_time">Real-time</option>
                <option value="hourly">Hourly</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
              <button
                onClick={createSchedule}
                disabled={schedulingSubmitting}
                className="rounded-md bg-accent px-3 py-1 text-xs font-medium text-white disabled:opacity-60"
              >
                {schedulingSubmitting ? 'Scheduling…' : 'Schedule'}
              </button>
            </div>
          ) : (
            <p className="mt-2 text-xs text-ink-soft">No schedule set yet.</p>
          )}
        </div>
      </div>

      <div>
        <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Executions</div>
        <div className="mt-2 overflow-hidden rounded-lg border border-line bg-surface">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
                <th className="px-3 py-2">Started</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Records</th>
                <th className="px-3 py-2">Exceptions</th>
                <th className="px-3 py-2">Log</th>
              </tr>
            </thead>
            <tbody>
              {executions.map((e) => (
                <tr key={e.execution_id} className="border-t border-line">
                  <td className="px-3 py-2 text-xs text-ink-soft">{new Date(e.started_at).toLocaleString()}</td>
                  <td className="px-3 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${EXECUTION_STYLES[e.status] ?? ''}`}>
                      {EXECUTION_LABELS[e.status] ?? e.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs tabular-nums">{e.records_analyzed ?? '—'}</td>
                  <td className="px-3 py-2 text-xs tabular-nums">{e.exceptions_found ?? '—'}</td>
                  <td className="max-w-md px-3 py-2 text-xs text-ink-soft">{e.execution_log ?? '—'}</td>
                </tr>
              ))}
              {executions.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-4 text-center text-xs text-ink-soft">
                    Not run yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {deletingRuleId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" onClick={() => setDeletingRuleId(null)}>
          <div className="w-full max-w-sm rounded-lg border border-line bg-surface p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="text-sm font-semibold text-ink">Delete test rule</div>
            <p className="mt-2 text-sm text-ink-soft">
              A reason is required — this becomes part of the audit trail explaining why this control's test logic was removed.
            </p>
            <textarea
              autoFocus
              placeholder="e.g. This client has no concept of dormant accounts — all access reviewed manually."
              value={deleteReason}
              onChange={(e) => setDeleteReason(e.target.value)}
              className="mt-2 w-full rounded-md border border-line px-2 py-1 text-sm"
              rows={3}
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                onClick={() => {
                  setDeletingRuleId(null)
                  setDeleteReason('')
                }}
                className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-ink hover:bg-bg"
              >
                Cancel
              </button>
              <button
                onClick={confirmDelete}
                disabled={!deleteReason.trim()}
                className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {rejectingRuleId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" onClick={() => setRejectingRuleId(null)}>
          <div className="w-full max-w-sm rounded-lg border border-line bg-surface p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="text-sm font-semibold text-ink">Reject test rule</div>
            <p className="mt-2 text-sm text-ink-soft">
              A reason is required — the rule goes back to the maker to fix and resubmit.
            </p>
            <textarea
              autoFocus
              placeholder="e.g. Threshold looks wrong — 365 days would miss most dormant accounts."
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              className="mt-2 w-full rounded-md border border-line px-2 py-1 text-sm"
              rows={3}
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                onClick={() => {
                  setRejectingRuleId(null)
                  setRejectReason('')
                }}
                className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-ink hover:bg-bg"
              >
                Cancel
              </button>
              <button
                onClick={confirmReject}
                disabled={!rejectReason.trim()}
                className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
              >
                Reject
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
