import { Fragment, useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { AUDIT_FRAMEWORK_ROLES } from '../auth/permissions'
import { RulePreview } from './RulePreview'
import { ExceptionExplanationBlock } from './ExceptionExplanation'
import type { ExceptionOut, MonitoringScheduleOut, TestExecutionOut, TestRuleOut } from '../types/api'

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
const NEEDS_ATTENTION_STATUSES = new Set(['mapping_required', 'not_testable', 'error'])
const EXECUTION_STATUS_EXPLANATIONS: Record<string, string> = {
  mapping_required: "This control has a rule, but not all of the information it needs has been approved and connected yet, so nothing could be checked.",
  not_testable: "This control needs a table that hasn't been found or connected yet, so it can't be checked at all.",
  insufficient_data: "The test ran, but there was no information to check — so there's nothing yet to judge this control by.",
  error: "The test couldn't run because of a technical problem (like a connection dropping), not because of anything wrong with the control.",
}
const EXECUTION_STATUS_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'exception', label: 'Exceptions' },
  { key: 'needs_attention', label: 'Needs attention' },
  { key: 'pass', label: 'Passed' },
  { key: 'error', label: 'Errors' },
] as const
type ExecutionFilterKey = (typeof EXECUTION_STATUS_FILTERS)[number]['key']

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

type RuleType = 'threshold' | 'duplicate' | 'missing_match' | 'cross_match_condition'

interface Props {
  organizationId: string
  auditTestId: string
}

export function TestEnginePanel({ organizationId, auditTestId }: Props) {
  const { hasRole, user: currentUser } = useAuth()
  const canManage = hasRole(...AUDIT_FRAMEWORK_ROLES)
  const [rules, setRules] = useState<TestRuleOut[]>([])
  const [schedules, setSchedules] = useState<MonitoringScheduleOut[]>([])
  const [executions, setExecutions] = useState<TestExecutionOut[]>([])
  const [exceptions, setExceptions] = useState<ExceptionOut[]>([])
  const [error, setError] = useState<string | null>(null)
  const [execView, setExecView] = useState<'current' | 'history'>('current')
  const [execHistoryPreset, setExecHistoryPreset] = useState<'today' | 'week' | 'month' | 'all' | 'custom'>('today')
  const [execFromDate, setExecFromDate] = useState(() => isoDate(new Date()))
  const [execToDate, setExecToDate] = useState(() => isoDate(new Date()))
  const [execFilter, setExecFilter] = useState<ExecutionFilterKey>('all')
  const [expandedExecutionId, setExpandedExecutionId] = useState<string | null>(null)

  const applyExecPreset = (preset: typeof execHistoryPreset) => {
    setExecHistoryPreset(preset)
    const now = new Date()
    if (preset === 'today') {
      setExecFromDate(isoDate(now))
      setExecToDate(isoDate(now))
    } else if (preset === 'week') {
      const start = new Date(now)
      start.setDate(start.getDate() - 6)
      setExecFromDate(isoDate(start))
      setExecToDate(isoDate(now))
    } else if (preset === 'month') {
      const start = new Date(now.getFullYear(), now.getMonth(), 1)
      setExecFromDate(isoDate(start))
      setExecToDate(isoDate(now))
    } else if (preset === 'all') {
      setExecFromDate('')
      setExecToDate('')
    }
  }
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
  const [cancellingRuleId, setCancellingRuleId] = useState<string | null>(null)
  const [ruleCancelReason, setRuleCancelReason] = useState('')

  const [schedulingError, setSchedulingError] = useState<string | null>(null)
  const [approvingScheduleId, setApprovingScheduleId] = useState<string | null>(null)
  const [rejectingScheduleId, setRejectingScheduleId] = useState<string | null>(null)
  const [scheduleRejectReason, setScheduleRejectReason] = useState('')
  const [cancellingScheduleId, setCancellingScheduleId] = useState<string | null>(null)
  const [scheduleCancelReason, setScheduleCancelReason] = useState('')

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
    apiClient.get<ExceptionOut[]>(`/organizations/${organizationId}/exceptions`).then((res) => setExceptions(res.data))
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

  // The author withdrawing their OWN still-pending submission — distinct
  // from rejecting someone else's, which the backend never allows the
  // author to do themselves.
  const confirmCancelRule = async () => {
    if (!cancellingRuleId || !ruleCancelReason.trim()) return
    await apiClient.post(`/test-rules/${cancellingRuleId}/cancel`, { reason: ruleCancelReason })
    setCancellingRuleId(null)
    setRuleCancelReason('')
    load()
  }

  const createSchedule = async () => {
    if (schedulingSubmitting) return
    setSchedulingSubmitting(true)
    setSchedulingError(null)
    try {
      await apiClient.post(`/organizations/${organizationId}/audit-tests/${auditTestId}/schedules`, { frequency, is_active: true })
      await load()
    } catch (err: any) {
      setSchedulingError(err?.response?.data?.detail ?? 'Could not request this schedule.')
    } finally {
      setSchedulingSubmitting(false)
    }
  }

  const approveSchedule = async (scheduleId: string) => {
    setApprovingScheduleId(scheduleId)
    setSchedulingError(null)
    try {
      await apiClient.post(`/schedules/${scheduleId}/approve`)
      await load()
    } catch (err: any) {
      setSchedulingError(err?.response?.data?.detail ?? 'Could not approve this schedule.')
    } finally {
      setApprovingScheduleId(null)
    }
  }

  const confirmRejectSchedule = async () => {
    if (!rejectingScheduleId || !scheduleRejectReason.trim()) return
    await apiClient.post(`/schedules/${rejectingScheduleId}/reject`, { reason: scheduleRejectReason })
    setRejectingScheduleId(null)
    setScheduleRejectReason('')
    await load()
  }

  // The requester withdrawing their OWN still-pending request — distinct
  // from rejecting someone else's, which the backend never allows the
  // requester to do themselves.
  const confirmCancelSchedule = async () => {
    if (!cancellingScheduleId || !scheduleCancelReason.trim()) return
    await apiClient.post(`/schedules/${cancellingScheduleId}/cancel`, { reason: scheduleCancelReason })
    setCancellingScheduleId(null)
    setScheduleCancelReason('')
    await load()
  }

  const visibleRules = rules.filter((r) => r.status !== 'deleted')
  // A test only ever has one LIVE schedule, plus at most one change
  // awaiting a second person's approval — anything else (rejected,
  // superseded) is history only, listing it here would just be clutter.
  const activeSchedule = schedules.find((s) => s.status === 'active') ?? null
  const pendingSchedule = schedules.find((s) => s.status === 'pending_approval') ?? null
  const supersededScheduleCount = schedules.filter((s) => s.status === 'superseded').length

  // The "propose a change" dropdown below always started at the hardcoded
  // 'daily' default and never synced to the real active schedule — so the
  // moment a pending request got approved (e.g. "hourly"), this dropdown
  // reappeared showing "Daily" right next to the correctly-still-hourly
  // active schedule. Nothing was actually changed, but it looked like it
  // was, and clicking the button without first noticing would have
  // silently submitted a real request to change it to Daily. Keep it
  // synced to whatever's actually active so it only ever starts from the
  // true current cadence.
  useEffect(() => {
    if (activeSchedule) setFrequency(activeSchedule.frequency)
  }, [activeSchedule?.schedule_id, activeSchedule?.frequency])
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
                        {r.status === 'pending_approval' && r.created_by === currentUser?.user_id && (
                          <button onClick={() => setCancellingRuleId(r.rule_id)} className="text-xs font-medium text-red-600 hover:underline">
                            Cancel request
                          </button>
                        )}
                        {r.status === 'pending_approval' && r.created_by !== currentUser?.user_id && (
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

          {activeSchedule && (
            <p className="mt-2 text-xs">
              {activeSchedule.frequency} · active · next run{' '}
              {activeSchedule.next_run ? new Date(activeSchedule.next_run).toLocaleString() : '—'}
              {activeSchedule.last_run && <> · last run {new Date(activeSchedule.last_run).toLocaleString()}</>}
            </p>
          )}

          {pendingSchedule && (
            <div className="mt-2 rounded-md border border-orange-300 bg-orange-50/60 px-2 py-1.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs">
                  <span className="font-bold text-ink">{pendingSchedule.frequency}</span>{' '}
                  <span className="text-ink-soft">
                    {activeSchedule ? '— proposed change, awaiting approval' : '— awaiting approval'}
                  </span>
                </span>
                {canManage && pendingSchedule.created_by === currentUser?.user_id && (
                  <button
                    onClick={() => setCancellingScheduleId(pendingSchedule.schedule_id)}
                    className="text-xs font-medium text-red-600 hover:underline"
                  >
                    Cancel request
                  </button>
                )}
                {canManage && pendingSchedule.created_by !== currentUser?.user_id && (
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => approveSchedule(pendingSchedule.schedule_id)}
                      disabled={approvingScheduleId === pendingSchedule.schedule_id}
                      className="text-xs font-medium text-accent-ink hover:underline disabled:opacity-60"
                    >
                      {approvingScheduleId === pendingSchedule.schedule_id ? 'Approving…' : 'Approve'}
                    </button>
                    <button
                      onClick={() => setRejectingScheduleId(pendingSchedule.schedule_id)}
                      className="text-xs font-medium text-red-600 hover:underline"
                    >
                      Reject
                    </button>
                  </div>
                )}
              </div>
              <p className="mt-1 text-[10px] text-ink-soft">
                A different authorized user must approve this before it takes effect — nothing runs on this cadence yet.
              </p>
            </div>
          )}

          {!activeSchedule && !pendingSchedule && (
            <p className="mt-2 text-xs text-ink-soft">No schedule set yet.</p>
          )}

          {supersededScheduleCount > 0 && (
            <p className="mt-1 text-[10px] text-ink-soft">
              {supersededScheduleCount} superseded schedule{supersededScheduleCount === 1 ? '' : 's'} hidden (kept for history only).
            </p>
          )}

          {canManage && !pendingSchedule && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <select value={frequency} onChange={(e) => setFrequency(e.target.value)} className="rounded-md border border-line px-2 py-1 text-xs">
                <option value="real_time">Real-time</option>
                <option value="hourly">Hourly</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
              <button
                onClick={createSchedule}
                disabled={schedulingSubmitting || (!!activeSchedule && frequency === activeSchedule.frequency)}
                className="rounded-md bg-accent px-3 py-1 text-xs font-medium text-white disabled:opacity-60"
              >
                {schedulingSubmitting ? 'Requesting…' : activeSchedule ? 'Request change' : 'Schedule'}
              </button>
              {activeSchedule && frequency === activeSchedule.frequency && (
                <span className="text-[10px] text-ink-soft">Already the active schedule — pick a different cadence to request a change.</span>
              )}
            </div>
          )}
          {schedulingError && <p className="mt-1 text-xs text-red-600">{schedulingError}</p>}
        </div>
      </div>

      <div>
        <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Executions</div>
        <p className="mt-1 text-xs text-ink-soft">
          {execView === 'current'
            ? 'The most recent run of this test — its status right now.'
            : 'Every run of this test — pick a day, a week, a month, or any custom range.'}{' '}
          A real problem is always recorded as an exception, never silently reinterpreted as a pass.
        </p>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <div className="flex gap-1 rounded-md bg-bg p-0.5 text-xs">
            <button
              onClick={() => setExecView('current')}
              className={`rounded px-2.5 py-1 font-medium ${execView === 'current' ? 'bg-surface text-ink shadow-sm' : 'text-ink-soft'}`}
            >
              Current
            </button>
            <button
              onClick={() => setExecView('history')}
              className={`rounded px-2.5 py-1 font-medium ${execView === 'history' ? 'bg-surface text-ink shadow-sm' : 'text-ink-soft'}`}
            >
              History
            </button>
          </div>
          {execView === 'history' && (
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
                    onClick={() => applyExecPreset(p.key)}
                    className={`rounded px-2.5 py-1 font-medium ${execHistoryPreset === p.key ? 'bg-surface text-ink shadow-sm' : 'text-ink-soft'}`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
              <label className="flex items-center gap-1 text-xs text-ink-soft">
                From
                <input
                  type="date"
                  value={execFromDate}
                  onChange={(e) => {
                    setExecHistoryPreset('custom')
                    setExecFromDate(e.target.value)
                  }}
                  className="rounded-md border border-line px-2 py-1 text-xs"
                />
              </label>
              <label className="flex items-center gap-1 text-xs text-ink-soft">
                To
                <input
                  type="date"
                  value={execToDate}
                  onChange={(e) => {
                    setExecHistoryPreset('custom')
                    setExecToDate(e.target.value)
                  }}
                  className="rounded-md border border-line px-2 py-1 text-xs"
                />
              </label>
            </>
          )}
        </div>

        {(() => {
          const sortedExecutions = [...executions].sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime())
          const base =
            execView === 'current'
              ? sortedExecutions.slice(0, 1)
              : sortedExecutions.filter((e) => {
                  const started = new Date(e.started_at)
                  if (execFromDate && started < new Date(execFromDate)) return false
                  if (execToDate && started > new Date(`${execToDate}T23:59:59`)) return false
                  return true
                })
          const filterCounts = {
            all: base.length,
            exception: base.filter((e) => e.status === 'exception').length,
            needs_attention: base.filter((e) => NEEDS_ATTENTION_STATUSES.has(e.status)).length,
            pass: base.filter((e) => e.status === 'pass').length,
            error: base.filter((e) => e.status === 'error').length,
          }
          const filteredExecutions = base.filter((e) => {
            if (execFilter === 'all') return true
            if (execFilter === 'needs_attention') return NEEDS_ATTENTION_STATUSES.has(e.status)
            return e.status === execFilter
          })

          return (
            <>
              {execView === 'history' && (
                <div className="mt-2 flex gap-1 rounded-md bg-bg p-0.5 text-xs w-fit">
                  {EXECUTION_STATUS_FILTERS.map((f) => (
                    <button
                      key={f.key}
                      onClick={() => setExecFilter(f.key)}
                      className={`rounded px-2.5 py-1 font-medium ${execFilter === f.key ? 'bg-surface text-ink shadow-sm' : 'text-ink-soft'}`}
                    >
                      {f.label} <span className="text-ink-faint">{filterCounts[f.key]}</span>
                    </button>
                  ))}
                </div>
              )}
              <div className="mt-2 overflow-x-auto rounded-lg border border-line bg-surface">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
                      <th className="px-3 py-2">Started</th>
                      <th className="px-3 py-2">Status</th>
                      <th className="px-3 py-2">Records</th>
                      <th className="px-3 py-2">Exceptions</th>
                      <th className="px-3 py-2">Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredExecutions.map((e) => {
                      // "Current" (a single row, the latest execution) matches by
                      // audit_test_id and open status instead of requiring the
                      // exact execution_id — a currently-open exception's
                      // execution_id moves forward to whichever run most recently
                      // re-detected it (see execution_service.record_execution_
                      // report), which can be a beat ahead of this panel's own,
                      // separately-fetched executions list on a fast-cycling
                      // schedule. "History" (many past rows) keeps the exact
                      // execution_id match, since each row's own reason should
                      // reflect what THAT specific run found, not every
                      // currently-open issue for the test.
                      const rowExceptions =
                        e.status !== 'exception'
                          ? []
                          : execView === 'current'
                            ? exceptions.filter((x) => x.audit_test_id === auditTestId && x.status !== 'resolved' && x.status !== 'closed')
                            : exceptions.filter((x) => x.execution_id === e.execution_id)
                      const isExpanded = expandedExecutionId === e.execution_id
                      const preview = rowExceptions[0]?.summary ?? rowExceptions[0]?.exception_description ?? ''
                      return (
                        <Fragment key={e.execution_id}>
                          <tr className="border-t border-line align-top">
                            <td className="px-3 py-2 text-xs text-ink-soft whitespace-nowrap">{new Date(e.started_at).toLocaleString()}</td>
                            <td className="px-3 py-2">
                              <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${EXECUTION_STYLES[e.status] ?? ''}`}>
                                {EXECUTION_LABELS[e.status] ?? e.status}
                              </span>
                            </td>
                            <td className="px-3 py-2 text-xs tabular-nums">{e.records_analyzed ?? '—'}</td>
                            <td className="px-3 py-2 text-xs tabular-nums">{e.exceptions_found ?? '—'}</td>
                            <td className="max-w-md px-3 py-2 text-xs text-ink-soft">
                              {rowExceptions.length === 0 ? (
                                e.execution_log || EXECUTION_STATUS_EXPLANATIONS[e.status] || '—'
                              ) : (
                                <>
                                  {preview}
                                  {rowExceptions.length > 1 && <span className="text-ink-faint"> (+{rowExceptions.length - 1} more)</span>}
                                  <button
                                    onClick={() => setExpandedExecutionId(isExpanded ? null : e.execution_id)}
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
                              <td colSpan={5} className="space-y-2 px-3 py-3">
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
                    {filteredExecutions.length === 0 && (
                      <tr>
                        <td colSpan={5} className="px-3 py-4 text-center text-xs text-ink-soft">
                          {execView === 'current' ? 'Not run yet.' : 'No test executions in this range.'}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </>
          )
        })()}
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

      {cancellingRuleId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" onClick={() => setCancellingRuleId(null)}>
          <div className="w-full max-w-sm rounded-lg border border-line bg-surface p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="text-sm font-semibold text-ink">Cancel your rule submission</div>
            <p className="mt-2 text-sm text-ink-soft">A reason is required — you can edit and resubmit it afterward.</p>
            <textarea
              autoFocus
              placeholder="e.g. Noticed a mistake in the join field, withdrawing to fix it."
              value={ruleCancelReason}
              onChange={(e) => setRuleCancelReason(e.target.value)}
              className="mt-2 w-full rounded-md border border-line px-2 py-1 text-sm"
              rows={3}
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                onClick={() => {
                  setCancellingRuleId(null)
                  setRuleCancelReason('')
                }}
                className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-ink hover:bg-bg"
              >
                Never mind
              </button>
              <button
                onClick={confirmCancelRule}
                disabled={!ruleCancelReason.trim()}
                className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
              >
                Cancel request
              </button>
            </div>
          </div>
        </div>
      )}

      {rejectingScheduleId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" onClick={() => setRejectingScheduleId(null)}>
          <div className="w-full max-w-sm rounded-lg border border-line bg-surface p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="text-sm font-semibold text-ink">Reject monitoring schedule</div>
            <p className="mt-2 text-sm text-ink-soft">
              A reason is required — the maker can request a different cadence afterward.
            </p>
            <textarea
              autoFocus
              placeholder="e.g. Real-time is overkill here — daily is enough for this control."
              value={scheduleRejectReason}
              onChange={(e) => setScheduleRejectReason(e.target.value)}
              className="mt-2 w-full rounded-md border border-line px-2 py-1 text-sm"
              rows={3}
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                onClick={() => {
                  setRejectingScheduleId(null)
                  setScheduleRejectReason('')
                }}
                className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-ink hover:bg-bg"
              >
                Cancel
              </button>
              <button
                onClick={confirmRejectSchedule}
                disabled={!scheduleRejectReason.trim()}
                className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
              >
                Reject
              </button>
            </div>
          </div>
        </div>
      )}

      {cancellingScheduleId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" onClick={() => setCancellingScheduleId(null)}>
          <div className="w-full max-w-sm rounded-lg border border-line bg-surface p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="text-sm font-semibold text-ink">Cancel your schedule request</div>
            <p className="mt-2 text-sm text-ink-soft">A reason is required — you can submit a new request afterward.</p>
            <textarea
              autoFocus
              placeholder="e.g. Requested the wrong cadence by mistake."
              value={scheduleCancelReason}
              onChange={(e) => setScheduleCancelReason(e.target.value)}
              className="mt-2 w-full rounded-md border border-line px-2 py-1 text-sm"
              rows={3}
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                onClick={() => {
                  setCancellingScheduleId(null)
                  setScheduleCancelReason('')
                }}
                className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-ink hover:bg-bg"
              >
                Never mind
              </button>
              <button
                onClick={confirmCancelSchedule}
                disabled={!scheduleCancelReason.trim()}
                className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
              >
                Cancel request
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
