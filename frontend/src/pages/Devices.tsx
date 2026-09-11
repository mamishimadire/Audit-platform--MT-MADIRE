import { Fragment, useEffect, useState, type FormEvent } from 'react'
import { apiClient, API_BASE_URL } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type {
  ApprovalStatus,
  ApprovedSoftwareOut,
  ComplianceCheckDetail,
  DeviceCommandOut,
  DeviceCommandType,
  DeviceCreatedOut,
  DeviceOut,
  DevicePolicyOut,
  DevicePolicyChangeOut,
  DeviceSoftwareOut,
  EligibleApproverOut,
  EnrichedSoftwareItem,
  PolicyClassification,
  RiskLevel,
  SoftwareClassification,
  SoftwareComplianceResult,
} from '../types/api'

const SOFTWARE_CLASSIFICATION_LABELS: Record<SoftwareClassification, string> = {
  approved: 'Approved',
  required: 'Required',
  restricted: 'Restricted',
  system_component: 'System component',
  ignored: 'Ignored',
  review_required: 'Review required',
  unknown: 'Unknown',
}

const SOFTWARE_CLASSIFICATION_STYLES: Record<SoftwareClassification, string> = {
  approved: 'bg-accent-soft text-accent-ink',
  required: 'bg-blue-50 text-blue-700',
  restricted: 'bg-red-50 text-red-700',
  system_component: 'bg-bg text-ink-soft',
  ignored: 'bg-bg text-ink-soft',
  review_required: 'bg-amber-50 text-amber-700',
  unknown: 'bg-bg text-ink-soft',
}

const COMPLIANCE_RESULT_LABELS: Record<SoftwareComplianceResult, string> = {
  compliant: '✓ Compliant',
  non_compliant: '✗ Non-compliant',
  outdated: '⚠ Outdated',
  review_required: 'Review required',
  not_evaluated: 'Not evaluated',
}

const COMPLIANCE_RESULT_STYLES: Record<SoftwareComplianceResult, string> = {
  compliant: 'text-accent-ink',
  non_compliant: 'text-red-600 font-medium',
  outdated: 'text-orange-600 font-medium',
  review_required: 'text-amber-700',
  not_evaluated: 'text-ink-soft',
}

const COMMAND_LABELS: Record<DeviceCommandType, string> = { run_check_now: 'Run check now', restart: 'Restart device' }

// Must match endpoint-agent/endpoint_agent/register.py's AGENT_VERSION —
// used only to explain an outdated-agent gap to the user, not to gate any
// actual functionality.
const CURRENT_AGENT_VERSION = '0.2.0'

// Must match the backend's devices:manage_policy permission holders —
// deliberately excludes Client IT Admin. Setting the compliance policy that
// judges a client's own devices, and issuing remote actions like restart,
// is an auditor/platform-side judgement call, not something the audited
// party controls unsupervised (segregation of duties).
const CAN_MANAGE_POLICY_ROLES = ['Platform Super Admin', 'Platform Admin', 'Audit Manager', 'IT/Audit Technical User', 'Device Manager']

const ONLINE_STATUSES = new Set(['online'])

// One shared set of button treatments so every lifecycle action reads at a
// glance: PRIMARY = the sanctioned way to complete a review step, DANGER =
// initiating something consequential, SECONDARY = a neutral/declining
// action. Every variant defines its own disabled state explicitly — a
// disabled button must look inert, not just a lighter version of "active."
const BTN_PRIMARY = 'rounded-md border border-transparent bg-accent px-2.5 py-1 text-xs font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:border-line disabled:bg-bg disabled:text-ink-faint'
const BTN_DANGER_OUTLINE = 'rounded-md border border-red-300 bg-white px-2.5 py-1 text-xs font-medium text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:border-line disabled:bg-bg disabled:text-ink-faint'
const BTN_SECONDARY = 'rounded-md border border-line bg-white px-2.5 py-1 text-xs font-medium text-ink hover:bg-bg disabled:cursor-not-allowed disabled:border-line disabled:bg-bg disabled:text-ink-faint'

const COMPLIANCE_STYLES: Record<string, string> = {
  compliant: 'bg-accent-soft text-accent-ink',
  non_compliant: 'bg-red-50 text-red-700',
  unknown: 'bg-bg text-ink-soft',
}
const COMPLIANCE_LABELS: Record<string, string> = {
  compliant: 'Compliant',
  non_compliant: 'Non-compliant',
  unknown: 'Unknown',
}

function StatusDot({ online }: { online: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`h-2 w-2 rounded-full ${online ? 'bg-accent' : 'bg-ink-soft'}`} />
      {online ? 'Online' : 'Offline'}
    </span>
  )
}

function ChecklistItem({ label, value }: { label: string; value: boolean | null }) {
  const icon = value === null ? '—' : value ? '✓' : '✗'
  const color = value === null ? 'text-ink-soft' : value ? 'text-accent-ink' : 'text-red-600'
  return (
    <div className={`flex items-center gap-2 text-xs ${color}`}>
      <span className="font-bold">{icon}</span>
      <span>{label}{value === null ? ' — unknown' : ''}</span>
    </div>
  )
}

const SOFTWARE_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'approved', label: 'Approved' },
  { key: 'review', label: 'Review required' },
  { key: 'noncompliant', label: 'Non-compliant' },
] as const
type SoftwareFilterKey = (typeof SOFTWARE_FILTERS)[number]['key']

// entries now includes every version/status (pending, approved, rejected,
// superseded) for reviewers to see history — reclassifying an item should
// only ever touch its current "live" row: the approved one, or if a
// reclassification is already pending, the newest pending version.
function matchApprovedEntry(item: EnrichedSoftwareItem, entries: ApprovedSoftwareOut[]): ApprovedSoftwareOut | null {
  const candidates = entries.filter(
    (e) =>
      e.app_name.trim().toLowerCase() === item.name.trim().toLowerCase() &&
      (e.approval_status === 'approved' || e.approval_status === 'pending_approval') &&
      (!e.publisher || (item.publisher ?? '').trim().toLowerCase() === e.publisher.trim().toLowerCase()),
  )
  if (candidates.length === 0) return null
  return candidates.reduce((latest, c) => (c.version > latest.version ? c : latest))
}

function ClassifySelect({ onPick, busy }: { onPick: (c: PolicyClassification) => void; busy: boolean }) {
  return (
    <select
      value=""
      disabled={busy}
      onChange={(e) => {
        const value = e.target.value as PolicyClassification
        if (value) onPick(value)
        e.target.value = ''
      }}
      className="rounded-md border border-line px-1.5 py-0.5 text-xs font-medium text-accent-ink disabled:opacity-60"
    >
      <option value="">{busy ? 'Saving…' : 'Classify…'}</option>
      {(Object.keys(CLASSIFICATION_LABELS) as PolicyClassification[]).map((c) => (
        <option key={c} value={c}>
          {CLASSIFICATION_LABELS[c]}
        </option>
      ))}
    </select>
  )
}

function DeviceRow({
  device,
  canEnrol,
  canRequestRevoke,
  canApproveRevoke,
  canRequestDelete,
  canApproveDelete,
  canCommandSafe,
  canCommandDisruptive,
  canManagePolicy,
  currentUserId,
  eligibleRevokeApprovers,
  eligibleDeleteApprovers,
  eligibleClassificationApprovers,
  onRevoked,
}: {
  device: DeviceOut
  canEnrol: boolean
  canRequestRevoke: boolean
  canApproveRevoke: boolean
  canRequestDelete: boolean
  canApproveDelete: boolean
  canCommandSafe: boolean
  canCommandDisruptive: boolean
  canManagePolicy: boolean
  currentUserId: string | undefined
  eligibleRevokeApprovers: EligibleApproverOut[]
  eligibleDeleteApprovers: EligibleApproverOut[]
  eligibleClassificationApprovers: EligibleApproverOut[]
  onRevoked: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [software, setSoftware] = useState<DeviceSoftwareOut | 'none' | null>(null)
  const [softwareSearch, setSoftwareSearch] = useState('')
  const [softwareFilter, setSoftwareFilter] = useState<SoftwareFilterKey>('all')
  const [complianceDetail, setComplianceDetail] = useState<ComplianceCheckDetail[] | null>(null)
  const [showPassingChecks, setShowPassingChecks] = useState(false)
  const [approvedSoftware, setApprovedSoftware] = useState<ApprovedSoftwareOut[]>([])
  const [classifyingKey, setClassifyingKey] = useState<string | null>(null)
  const [justClassifiedKey, setJustClassifiedKey] = useState<string | null>(null)
  const [classificationBusyId, setClassificationBusyId] = useState<string | null>(null)
  const [rejectingClassificationId, setRejectingClassificationId] = useState<string | null>(null)
  const [classificationRejectReason, setClassificationRejectReason] = useState('')
  const [classificationError, setClassificationError] = useState<string | null>(null)
  const [commands, setCommands] = useState<DeviceCommandOut[]>([])
  const [issuingCommand, setIssuingCommand] = useState<DeviceCommandType | null>(null)
  const [commandError, setCommandError] = useState<string | null>(null)
  const [actionBusy, setActionBusy] = useState(false)
  const [lifecycleAction, setLifecycleAction] = useState<
    'request-revocation' | 'reject-revocation' | 'request-deletion' | 'reject-deletion' | null
  >(null)
  const [lifecycleReason, setLifecycleReason] = useState('')
  const [lifecycleError, setLifecycleError] = useState<string | null>(null)
  const online = ONLINE_STATUSES.has(device.status)

  // A stale error from a previous attempt (e.g. a double-click before the
  // button set updated) must not keep showing once the device's actual
  // status has moved on — otherwise it reads as a current problem forever.
  useEffect(() => {
    setLifecycleError(null)
  }, [device.status])

  const runLifecycleAction = async (path: string, body?: { reason: string }) => {
    setActionBusy(true)
    setLifecycleError(null)
    try {
      await apiClient.post(`/devices/${device.device_id}/${path}`, body)
      setLifecycleAction(null)
      setLifecycleReason('')
      onRevoked()
    } catch (err: any) {
      setLifecycleError(err?.response?.data?.detail ?? 'That action could not be completed.')
    } finally {
      setActionBusy(false)
    }
  }

  const submitLifecycleDialog = () => {
    if (lifecycleAction === 'request-revocation') return runLifecycleAction('revocation/request', { reason: lifecycleReason })
    if (lifecycleAction === 'reject-revocation') return runLifecycleAction('revocation/reject', { reason: lifecycleReason })
    if (lifecycleAction === 'request-deletion') return runLifecycleAction('deletion/request', { reason: lifecycleReason })
    if (lifecycleAction === 'reject-deletion') return runLifecycleAction('deletion/reject', { reason: lifecycleReason })
  }

  const approveRevocation = () => runLifecycleAction('revocation/approve')
  const approveDeletion = () => runLifecycleAction('deletion/approve')

  const [newCode, setNewCode] = useState<{ registration_code: string; registration_code_expires_at: string } | null>(null)
  const regenerateCode = async () => {
    setActionBusy(true)
    try {
      const res = await apiClient.post<{ registration_code: string; registration_code_expires_at: string }>(
        `/devices/${device.device_id}/regenerate-code`,
      )
      setNewCode(res.data)
      onRevoked()
    } finally {
      setActionBusy(false)
    }
  }

  const loadCommands = () =>
    apiClient
      .get<DeviceCommandOut[]>(`/organizations/${device.organization_id}/devices/${device.device_id}/commands`)
      .then((res) => setCommands(res.data))

  const loadSoftware = () =>
    apiClient
      .get<DeviceSoftwareOut>(`/organizations/${device.organization_id}/devices/${device.device_id}/software`)
      .then((res) => setSoftware(res.data))
      .catch(() => setSoftware('none'))

  const loadComplianceDetail = () =>
    apiClient
      .get<ComplianceCheckDetail[]>(`/organizations/${device.organization_id}/devices/${device.device_id}/compliance-detail`)
      .then((res) => setComplianceDetail(res.data))

  const loadApprovedSoftware = () =>
    apiClient
      .get<ApprovedSoftwareOut[]>(`/organizations/${device.organization_id}/approved-software`)
      .then((res) => setApprovedSoftware(res.data))

  const toggle = () => {
    if (!expanded && software === null) {
      loadSoftware()
      loadComplianceDetail()
      loadCommands()
      if (canManagePolicy) loadApprovedSoftware()
    }
    setExpanded(!expanded)
  }

  const issueCommand = async (command_type: DeviceCommandType) => {
    setIssuingCommand(command_type)
    setCommandError(null)
    try {
      await apiClient.post(`/organizations/${device.organization_id}/devices/${device.device_id}/commands`, { command_type })
      loadCommands()
    } catch (err: any) {
      if (err?.response?.status === 403) {
        setCommandError("You don't have permission to issue remote device commands.")
      } else {
        setCommandError('Could not queue this command.')
      }
    } finally {
      setIssuingCommand(null)
    }
  }

  const classifyOne = async (item: EnrichedSoftwareItem, classification: PolicyClassification) => {
    const existing = matchApprovedEntry(item, approvedSoftware)
    const payload = { app_name: item.name, publisher: item.publisher, classification, risk_level: existing?.risk_level ?? 'medium' }
    if (existing) {
      await apiClient.patch(`/organizations/${device.organization_id}/approved-software/${existing.approved_software_id}`, payload)
    } else {
      await apiClient.post(`/organizations/${device.organization_id}/approved-software`, payload)
    }
  }

  const classifyItem = async (item: EnrichedSoftwareItem, classification: PolicyClassification) => {
    const key = `${item.name}::${item.publisher ?? ''}`
    setClassifyingKey(key)
    try {
      await classifyOne(item, classification)
      await Promise.all([loadApprovedSoftware(), loadSoftware()])
      setJustClassifiedKey(key)
      setTimeout(() => setJustClassifiedKey(null), 2000)
    } finally {
      setClassifyingKey(null)
    }
  }

  const classifyGroup = async (items: EnrichedSoftwareItem[], classification: PolicyClassification) => {
    const key = `group::${items.map((i) => i.name).join(',')}`
    setClassifyingKey(key)
    try {
      for (const item of items) {
        await classifyOne(item, classification)
      }
      await Promise.all([loadApprovedSoftware(), loadSoftware()])
      setJustClassifiedKey(key)
      setTimeout(() => setJustClassifiedKey(null), 2000)
    } finally {
      setClassifyingKey(null)
    }
  }

  const latestCommand = commands[0]

  const namesOf = (approvers: EligibleApproverOut[]) =>
    approvers.length === 0 ? 'an authorized user' : approvers.map((a) => `${a.first_name} ${a.last_name}`).join(', ')
  const revokeApproverNames = namesOf(eligibleRevokeApprovers)
  const deleteApproverNames = namesOf(eligibleDeleteApprovers)
  const classificationApproverNames = namesOf(eligibleClassificationApprovers)

  // The compliance-relevant classification/compliance columns never reflect
  // a pending submission (see software_compliance_service — only 'approved'
  // rows count) — this finds that pending row so the UI can say so plainly
  // instead of leaving the reviewer to wonder why nothing changed yet.
  const findPendingEntryFor = (item: EnrichedSoftwareItem): ApprovedSoftwareOut | undefined =>
    approvedSoftware.find(
      (e) =>
        e.approval_status === 'pending_approval' &&
        e.app_name.trim().toLowerCase() === item.name.trim().toLowerCase() &&
        (!e.publisher || (item.publisher ?? '').trim().toLowerCase() === e.publisher.trim().toLowerCase()),
    )

  const approveClassificationEntry = async (approvedSoftwareId: string) => {
    setClassificationBusyId(approvedSoftwareId)
    setClassificationError(null)
    try {
      await apiClient.post(`/organizations/${device.organization_id}/approved-software/${approvedSoftwareId}/approve`)
      await Promise.all([loadApprovedSoftware(), loadSoftware()])
    } catch (err: any) {
      setClassificationError(err?.response?.data?.detail ?? 'Could not approve this classification.')
    } finally {
      setClassificationBusyId(null)
    }
  }

  const submitRejectClassification = async () => {
    if (!rejectingClassificationId) return
    const id = rejectingClassificationId
    setClassificationBusyId(id)
    setClassificationError(null)
    try {
      await apiClient.post(`/organizations/${device.organization_id}/approved-software/${id}/reject`, { reason: classificationRejectReason })
      setRejectingClassificationId(null)
      setClassificationRejectReason('')
      await Promise.all([loadApprovedSoftware(), loadSoftware()])
    } catch (err: any) {
      setClassificationError(err?.response?.data?.detail ?? 'Could not reject this classification.')
    } finally {
      setClassificationBusyId(null)
    }
  }

  const searchedSoftware =
    software && software !== 'none'
      ? software.items.filter((item) => item.name.toLowerCase().includes(softwareSearch.trim().toLowerCase()))
      : []

  const counts = {
    all: searchedSoftware.length,
    approved: searchedSoftware.filter((i) => i.classification === 'approved').length,
    review: searchedSoftware.filter((i) => i.classification === 'unknown' || i.classification === 'review_required').length,
    noncompliant: searchedSoftware.filter((i) => i.compliance_result === 'non_compliant' || i.compliance_result === 'outdated').length,
  }

  const filteredSoftware = searchedSoftware.filter((item) => {
    if (softwareFilter === 'approved') return item.classification === 'approved'
    if (softwareFilter === 'review') return item.classification === 'unknown' || item.classification === 'review_required'
    if (softwareFilter === 'noncompliant') return item.compliance_result === 'non_compliant' || item.compliance_result === 'outdated'
    return true
  })

  const groups: { publisher: string; items: EnrichedSoftwareItem[] }[] = []
  for (const item of filteredSoftware) {
    const publisher = item.publisher?.trim() || 'No publisher on file'
    let group = groups.find((g) => g.publisher === publisher)
    if (!group) {
      group = { publisher, items: [] }
      groups.push(group)
    }
    group.items.push(item)
  }
  groups.sort((a, b) => a.publisher.localeCompare(b.publisher))

  const failingChecks = complianceDetail?.filter((c) => c.state === 'fail') ?? []
  const otherChecks = complianceDetail?.filter((c) => c.state !== 'fail') ?? []

  return (
    <Fragment>
      <tr className="border-t border-line">
        <td className="px-4 py-2">
          <button onClick={toggle} className="font-medium text-ink hover:underline">
            {device.device_name}
          </button>
        </td>
        <td className="px-4 py-2 text-ink-soft">{device.assigned_user_name ?? '—'}</td>
        <td className="px-4 py-2 text-ink-soft">
          {device.status === 'deregistered' ? (
            'Deregistered'
          ) : device.status === 'pending_revocation' ? (
            <span className="font-medium text-orange-600">Pending revocation</span>
          ) : device.status === 'pending_deletion' ? (
            <span className="font-medium text-orange-600">Pending deletion</span>
          ) : (
            <StatusDot online={online} />
          )}
        </td>
        <td className="px-4 py-2 text-ink-soft">{device.agent_version ?? '—'}</td>
        <td className="px-4 py-2">
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${COMPLIANCE_STYLES[device.compliance_status]}`}>
            {COMPLIANCE_LABELS[device.compliance_status]}
          </span>
        </td>
        <td className="px-4 py-2 text-ink-soft">{device.last_heartbeat ? new Date(device.last_heartbeat).toLocaleString() : 'never'}</td>
        <td className="px-4 py-2 text-right">
          {(canEnrol || canRequestRevoke || canApproveRevoke || canRequestDelete || canApproveDelete) && (
            <div className="flex flex-col items-end gap-1">
              {device.status === 'pending_revocation' && (
                <p className="max-w-[18rem] text-right text-xs text-ink-soft">
                  <span className="font-medium text-ink">Reason:</span> {device.revocation_reason ?? '—'}
                </p>
              )}
              {device.status === 'pending_deletion' && (
                <p className="max-w-[18rem] text-right text-xs text-ink-soft">
                  <span className="font-medium text-ink">Reason:</span> {device.deletion_reason ?? '—'}
                </p>
              )}
              <div className="flex items-center justify-end gap-2">
                {device.status === 'pending_revocation' ? (
                  canApproveRevoke && device.revocation_requested_by !== currentUserId ? (
                    <>
                      <button onClick={approveRevocation} disabled={actionBusy} className={BTN_PRIMARY}>
                        Approve revocation
                      </button>
                      <button onClick={() => setLifecycleAction('reject-revocation')} disabled={actionBusy} className={BTN_SECONDARY}>
                        Reject
                      </button>
                    </>
                  ) : (
                    <span className="text-xs text-ink-faint">
                      {canApproveRevoke ? 'You requested this — a different approver is needed' : 'Awaiting approval'}
                    </span>
                  )
                ) : device.status === 'pending_deletion' ? (
                  canApproveDelete && device.deletion_requested_by !== currentUserId ? (
                    <>
                      <button onClick={approveDeletion} disabled={actionBusy} className={BTN_PRIMARY}>
                        Approve deletion
                      </button>
                      <button onClick={() => setLifecycleAction('reject-deletion')} disabled={actionBusy} className={BTN_SECONDARY}>
                        Reject
                      </button>
                    </>
                  ) : (
                    <span className="text-xs text-ink-faint">
                      {canApproveDelete ? 'You requested this — a different approver is needed' : 'Awaiting approval'}
                    </span>
                  )
                ) : (
                  <>
                    {device.status === 'deregistered' ? (
                      canEnrol && (
                        <button onClick={regenerateCode} disabled={actionBusy} className={BTN_PRIMARY}>
                          Re-register
                        </button>
                      )
                    ) : (
                      canRequestRevoke && (
                        <button onClick={() => setLifecycleAction('request-revocation')} disabled={actionBusy} className={BTN_DANGER_OUTLINE}>
                          Request revocation
                        </button>
                      )
                    )}
                    {canRequestDelete && (
                      <button onClick={() => setLifecycleAction('request-deletion')} disabled={actionBusy} className={BTN_DANGER_OUTLINE}>
                        Request deletion
                      </button>
                    )}
                  </>
                )}
              </div>
              {lifecycleError && <p className="max-w-[16rem] text-right text-xs text-red-600">{lifecycleError}</p>}
            </div>
          )}
        </td>
      </tr>
      <ConfirmDialog
        open={lifecycleAction === 'request-revocation'}
        title="Request device revocation"
        message={`Request revocation of "${device.device_name}"? This goes to ${revokeApproverNames} for approval before the device actually stops reporting — you won't be able to approve your own request.`}
        confirmLabel="Request revocation"
        danger
        reasonRequired
        reasonValue={lifecycleReason}
        onReasonChange={setLifecycleReason}
        reasonPlaceholder="Why is this device being revoked?"
        onConfirm={submitLifecycleDialog}
        onCancel={() => {
          setLifecycleAction(null)
          setLifecycleReason('')
        }}
      />
      <ConfirmDialog
        open={lifecycleAction === 'reject-revocation'}
        title="Reject revocation request"
        message={`Reject the pending revocation request for "${device.device_name}"?`}
        confirmLabel="Reject"
        reasonRequired
        reasonValue={lifecycleReason}
        onReasonChange={setLifecycleReason}
        reasonPlaceholder="Why is this request being rejected?"
        onConfirm={submitLifecycleDialog}
        onCancel={() => {
          setLifecycleAction(null)
          setLifecycleReason('')
        }}
      />
      <ConfirmDialog
        open={lifecycleAction === 'request-deletion'}
        title="Request device deletion"
        message={`Request deletion of "${device.device_name}"? This goes to ${deleteApproverNames} for approval — you won't be able to approve your own request. Once approved, the device stops reporting and moves to Deleted devices; its history and past exceptions/findings are kept, but it can never be remediated again.`}
        confirmLabel="Request deletion"
        danger
        reasonRequired
        reasonValue={lifecycleReason}
        onReasonChange={setLifecycleReason}
        reasonPlaceholder="Why is this device being deleted?"
        onConfirm={submitLifecycleDialog}
        onCancel={() => {
          setLifecycleAction(null)
          setLifecycleReason('')
        }}
      />
      <ConfirmDialog
        open={lifecycleAction === 'reject-deletion'}
        title="Reject deletion request"
        message={`Reject the pending deletion request for "${device.device_name}"?`}
        confirmLabel="Reject"
        reasonRequired
        reasonValue={lifecycleReason}
        onReasonChange={setLifecycleReason}
        reasonPlaceholder="Why is this request being rejected?"
        onConfirm={submitLifecycleDialog}
        onCancel={() => {
          setLifecycleAction(null)
          setLifecycleReason('')
        }}
      />
      <ConfirmDialog
        open={rejectingClassificationId !== null}
        title="Reject classification"
        message="Reject this classification? It will never take effect — the person who submitted it can see why and resubmit if appropriate."
        confirmLabel="Reject"
        reasonRequired
        reasonValue={classificationRejectReason}
        onReasonChange={setClassificationRejectReason}
        reasonPlaceholder="Why is this classification being rejected?"
        onConfirm={submitRejectClassification}
        onCancel={() => {
          setRejectingClassificationId(null)
          setClassificationRejectReason('')
        }}
      />
      {newCode && (
        <tr className="border-t border-line bg-accent-soft">
          <td colSpan={7} className="px-6 py-3 text-sm">
            <div className="font-semibold text-accent-ink">
              New registration code — single use, expires in 15 minutes
            </div>
            <div className="mt-1 font-mono text-lg tracking-wider text-ink">{newCode.registration_code}</div>
            <button onClick={() => setNewCode(null)} className="mt-1 text-xs font-medium text-accent-ink hover:underline">
              Dismiss
            </button>
          </td>
        </tr>
      )}
      {expanded && (
        <tr className="border-t border-line bg-bg">
          <td colSpan={7} className="px-6 py-3">
            <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">
              {device.hostname ?? device.device_name} {device.os_name ? `— ${device.os_name} ${device.os_version ?? ''}` : ''}
            </div>

            <div className="mt-2">
              {complianceDetail === null && <p className="text-xs text-ink-soft">Loading compliance checks…</p>}
              {complianceDetail !== null && failingChecks.length === 0 && otherChecks.length === 0 && (
                <p className="text-xs text-ink-soft">No compliance checks are enabled for this organization.</p>
              )}
              {failingChecks.map((check) => (
                <div key={check.field} className="mb-1.5 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2">
                  <span className="mt-0.5 text-red-600">●</span>
                  <div>
                    <div className="text-xs font-semibold text-red-700">{check.label}</div>
                    {check.detected_at && (
                      <div className="mt-0.5 text-[11px] text-red-700/80">
                        Non-compliant since {new Date(check.detected_at).toLocaleString()}
                        {check.occurrence_count ? ` — seen on ${check.occurrence_count} consecutive check-ins` : ''}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {otherChecks.length > 0 && (
                <div>
                  <button
                    onClick={() => setShowPassingChecks((v) => !v)}
                    className="flex w-full items-center justify-between rounded-md border border-accent-soft bg-accent-soft/40 px-3 py-1.5 text-xs text-accent-ink"
                  >
                    <span>
                      {otherChecks.filter((c) => c.state === 'pass').length} of {otherChecks.length} additional check
                      {otherChecks.length === 1 ? '' : 's'} passing
                    </span>
                    <span className="font-medium">{showPassingChecks ? 'Hide detail ▴' : 'Show detail ▾'}</span>
                  </button>
                  {showPassingChecks && (
                    <div className="mt-1 space-y-1 px-1">
                      {otherChecks.map((check) => (
                        <ChecklistItem key={check.field} label={check.label} value={check.state === 'unknown' ? null : true} />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="mt-3 border-t border-line pt-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">
                  Installed software
                  {software && software !== 'none' && (
                    <span className="ml-1 normal-case text-ink-soft">
                      ({software.items.length}, as of {new Date(software.collected_at).toLocaleString()})
                    </span>
                  )}
                </div>
                {software && software !== 'none' && software.items.length > 5 && (
                  <input
                    placeholder="Filter software…"
                    value={softwareSearch}
                    onChange={(e) => setSoftwareSearch(e.target.value)}
                    className="rounded-md border border-line px-2 py-1 text-xs"
                  />
                )}
              </div>
              {software === null && <p className="mt-2 text-xs text-ink-soft">Loading…</p>}
              {software === 'none' && (
                <p className="mt-2 text-xs text-ink-soft">
                  No software inventory reported yet by this device.
                  {device.agent_version && device.agent_version !== CURRENT_AGENT_VERSION && (
                    <>
                      {' '}
                      This device is running agent v{device.agent_version}, which predates software inventory
                      collection (added in v{CURRENT_AGENT_VERSION}) — reinstall using the Download button above to
                      get it.
                    </>
                  )}
                </p>
              )}
              {software && software !== 'none' && (
                <>
                  <div className="mt-2 flex gap-1 rounded-md bg-bg p-0.5 text-xs">
                    {SOFTWARE_FILTERS.map((f) => (
                      <button
                        key={f.key}
                        onClick={() => setSoftwareFilter(f.key)}
                        className={`rounded px-2 py-1 font-medium ${softwareFilter === f.key ? 'bg-surface text-ink shadow-sm' : 'text-ink-soft'}`}
                      >
                        {f.label} <span className="text-ink-faint">{counts[f.key]}</span>
                      </button>
                    ))}
                  </div>
                  {!canManagePolicy && (
                    <p className="mt-2 text-[11px] text-ink-soft">
                      Classification is managed by the audit team, not from this account — see your organization's
                      auditor to request a change.
                    </p>
                  )}
                  {classificationError && <p className="mt-2 text-xs text-red-600">{classificationError}</p>}
                  <div className="mt-2 max-h-80 overflow-y-auto overflow-x-auto rounded-md border border-line">
                    <table className="w-full text-left text-xs">
                      <colgroup>
                        <col className="w-[34%]" />
                        <col className="w-[16%]" />
                        <col className="w-[18%]" />
                        <col className="w-[18%]" />
                        <col className="w-[14%]" />
                      </colgroup>
                      <thead>
                        <tr className="border-b border-line bg-bg text-[11px] font-semibold uppercase tracking-wide text-ink-soft">
                          <th className="px-3 py-1.5 font-semibold">Application</th>
                          <th className="px-3 py-1.5 font-semibold">Version</th>
                          <th className="px-3 py-1.5 font-semibold">Classification</th>
                          <th className="px-3 py-1.5 font-semibold">Compliance</th>
                          <th className="px-3 py-1.5 font-semibold text-right">{canManagePolicy ? 'Action' : ''}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {groups.map((group) => {
                          const groupKey = `group::${group.items.map((i) => i.name).join(',')}`
                          const unclassifiedCount = group.items.filter((i) => i.classification === 'unknown').length
                          return (
                            <Fragment key={group.publisher}>
                              <tr className="border-t border-line bg-bg">
                                <td colSpan={5} className="px-3 py-1.5">
                                  <div className="flex items-center gap-2">
                                    <span className="font-semibold text-ink-soft">{group.publisher}</span>
                                    <span className="text-ink-faint">
                                      · {group.items.length} application{group.items.length === 1 ? '' : 's'}
                                      {unclassifiedCount > 0 ? `, ${unclassifiedCount} unclassified` : ', classified'}
                                    </span>
                                    {canManagePolicy && group.items.length > 1 && (
                                      <span className="ml-auto flex items-center gap-1.5">
                                        <span className="text-[11px] font-normal text-ink-faint">Classify all as:</span>
                                        <ClassifySelect
                                          busy={classifyingKey === groupKey}
                                          onPick={(c) => classifyGroup(group.items, c)}
                                        />
                                        {justClassifiedKey === groupKey && <span className="text-[11px] font-medium text-accent-ink">✓ Submitted for approval</span>}
                                      </span>
                                    )}
                                  </div>
                                </td>
                              </tr>
                              {group.items.map((item, i) => {
                                const itemKey = `${item.name}::${item.publisher ?? ''}`
                                const pending = findPendingEntryFor(item)
                                return (
                                  <tr key={`${item.name}-${i}`} className="border-t border-line-soft">
                                    <td className="px-3 py-1.5 text-ink">{item.name}</td>
                                    <td className="px-3 py-1.5 text-ink-soft">{item.version ?? '—'}</td>
                                    <td className="px-3 py-1.5">
                                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${SOFTWARE_CLASSIFICATION_STYLES[item.classification]}`}>
                                        {SOFTWARE_CLASSIFICATION_LABELS[item.classification]}
                                      </span>
                                    </td>
                                    <td className={`px-3 py-1.5 text-xs ${COMPLIANCE_RESULT_STYLES[item.compliance_result]}`}>
                                      {COMPLIANCE_RESULT_LABELS[item.compliance_result]}
                                    </td>
                                    <td className="px-3 py-1.5 text-right">
                                      {pending ? (
                                        <div className="text-right">
                                          <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                                            Pending approval as {CLASSIFICATION_LABELS[pending.classification as PolicyClassification] ?? pending.classification}
                                          </span>
                                          <div className="mt-0.5 text-[10px] text-ink-faint">
                                            Sent to {classificationApproverNames} — compliance status updates once approved or rejected.
                                          </div>
                                          {canManagePolicy && pending.created_by !== currentUserId ? (
                                            <div className="mt-1 flex justify-end gap-1">
                                              <button
                                                onClick={() => approveClassificationEntry(pending.approved_software_id)}
                                                disabled={classificationBusyId === pending.approved_software_id}
                                                className={BTN_PRIMARY}
                                              >
                                                Approve
                                              </button>
                                              <button
                                                onClick={() => setRejectingClassificationId(pending.approved_software_id)}
                                                disabled={classificationBusyId === pending.approved_software_id}
                                                className={BTN_SECONDARY}
                                              >
                                                Reject
                                              </button>
                                            </div>
                                          ) : (
                                            canManagePolicy &&
                                            pending.created_by === currentUserId && (
                                              <div className="mt-0.5 text-[10px] text-ink-faint">You submitted this — a different approver must decide it.</div>
                                            )
                                          )}
                                        </div>
                                      ) : (
                                        canManagePolicy &&
                                        (justClassifiedKey === itemKey ? (
                                          <span className="text-[11px] font-medium text-accent-ink">✓ Submitted for approval</span>
                                        ) : (
                                          <ClassifySelect busy={classifyingKey === itemKey} onPick={(c) => classifyItem(item, c)} />
                                        ))
                                      )}
                                    </td>
                                  </tr>
                                )
                              })}
                            </Fragment>
                          )
                        })}
                      </tbody>
                    </table>
                    {groups.length === 0 && (
                      <p className="px-3 py-2 text-center text-ink-soft">No matches.</p>
                    )}
                  </div>
                </>
              )}
            </div>
            {(canCommandSafe || canCommandDisruptive) && (
              <div className="mt-3 border-t border-line pt-3">
                <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Remote actions</div>
                <p className="mt-1 text-xs text-ink-soft">
                  Picked up within about 10 seconds — the agent checks for commands separately from its 5-minute
                  full compliance sync, and only ever polls the platform, it never accepts an inbound push.
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {(Object.keys(COMMAND_LABELS) as DeviceCommandType[])
                    .filter((type) => (type === 'run_check_now' ? canCommandSafe : canCommandDisruptive))
                    .map((type) => (
                      <button
                        key={type}
                        onClick={() => issueCommand(type)}
                        disabled={issuingCommand !== null}
                        className={BTN_SECONDARY}
                      >
                        {issuingCommand === type ? 'Queuing…' : COMMAND_LABELS[type]}
                      </button>
                    ))}
                </div>
                {commandError && <p className="mt-1 text-xs text-red-600">{commandError}</p>}
                {latestCommand && (
                  <p className="mt-2 text-xs text-ink-soft">
                    Last: {COMMAND_LABELS[latestCommand.command_type as DeviceCommandType] ?? latestCommand.command_type} —{' '}
                    <span
                      className={
                        latestCommand.status === 'failed'
                          ? 'font-medium text-red-600'
                          : latestCommand.status === 'completed'
                            ? 'font-medium text-accent-ink'
                            : ''
                      }
                    >
                      {latestCommand.status}
                    </span>
                    {latestCommand.result_message ? ` — ${latestCommand.result_message}` : ''}
                  </p>
                )}
              </div>
            )}
          </td>
        </tr>
      )}
    </Fragment>
  )
}

const POLICY_LABELS: Record<keyof DevicePolicyOut, string> = {
  require_antivirus: 'Antivirus / real-time protection required',
  require_firewall: 'Firewall required (all profiles)',
  require_disk_encryption: 'Disk encryption (BitLocker) required',
  require_os_up_to_date: 'Operating system must be up to date',
  require_software_compliance: 'Software policy compliance required',
}

const POLICY_DESCRIPTIONS: Record<keyof DevicePolicyOut, string> = {
  require_antivirus: 'Fails if the OS reports real-time protection as disabled or the AV service is not running.',
  require_firewall: 'Fails if any network profile (domain, private, public) reports the firewall as off.',
  require_disk_encryption: 'Fails if the system volume is not encrypted or protection is suspended.',
  require_os_up_to_date: 'Fails if critical OS updates are pending beyond the configured grace period.',
  require_software_compliance:
    'Fails if any Restricted application is present, or any Required application is missing. Unclassified software never fails this check on its own — it stays in "Review required" until an authorised user classifies it.',
}

function DevicePolicyPanel({ organizationId }: { organizationId: string }) {
  const { hasRole, user } = useAuth()
  const [policy, setPolicy] = useState<DevicePolicyOut | null>(null)
  const [draft, setDraft] = useState<DevicePolicyOut | null>(null)
  const [changes, setChanges] = useState<DevicePolicyChangeOut[]>([])
  const [eligibleApprovers, setEligibleApprovers] = useState<EligibleApproverOut[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [rejectingId, setRejectingId] = useState<string | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const canApprove = hasRole('Platform Super Admin', 'Audit Manager')
  const pending = changes.find((change) => change.approval_status === 'pending_approval') ?? null
  const hasDraftChanges =
    policy !== null && draft !== null && (Object.keys(POLICY_LABELS) as (keyof DevicePolicyOut)[]).some((key) => policy[key] !== draft[key])
  const approverNames = eligibleApprovers.length === 0 ? 'an authorized policy approver' : eligibleApprovers.map((a) => `${a.first_name} ${a.last_name}`).join(', ')

  const load = async () => {
    const [policyResponse, changesResponse, approversResponse] = await Promise.all([
      apiClient.get<DevicePolicyOut>(`/organizations/${organizationId}/device-policy`),
      apiClient.get<DevicePolicyChangeOut[]>(`/organizations/${organizationId}/device-policy/changes`),
      apiClient.get<EligibleApproverOut[]>(`/organizations/${organizationId}/devices/eligible-approvers`, { params: { action: 'policy' } }),
    ])
    setPolicy(policyResponse.data)
    setDraft(policyResponse.data)
    setChanges(changesResponse.data)
    setEligibleApprovers(approversResponse.data.filter((approver) => approver.user_id !== user?.user_id))
  }

  useEffect(() => {
    load().catch(() => setError('Could not load the device compliance policy.'))
  }, [organizationId])

  const toggleDraft = (key: keyof DevicePolicyOut) => {
    if (!draft || pending) return
    setDraft({ ...draft, [key]: !draft[key] })
  }

  const submit = async () => {
    if (!draft || !hasDraftChanges) return
    setSubmitting(true)
    setError(null)
    try {
      await apiClient.put<DevicePolicyChangeOut>(`/organizations/${organizationId}/device-policy`, draft)
      await load()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not submit the policy change for approval.')
    } finally {
      setSubmitting(false)
    }
  }

  const approve = async (policyChangeId: string) => {
    setBusyId(policyChangeId)
    setError(null)
    try {
      await apiClient.post(`/organizations/${organizationId}/device-policy/changes/${policyChangeId}/approve`)
      await load()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not approve this policy change.')
    } finally {
      setBusyId(null)
    }
  }

  const reject = async () => {
    if (!rejectingId) return
    setBusyId(rejectingId)
    setError(null)
    try {
      await apiClient.post(`/organizations/${organizationId}/device-policy/changes/${rejectingId}/reject`, { reason: rejectReason })
      setRejectingId(null)
      setRejectReason('')
      await load()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not reject this policy change.')
    } finally {
      setBusyId(null)
    }
  }

  if (!policy || !draft) return null

  const changedKeys = pending
    ? (Object.keys(POLICY_LABELS) as (keyof DevicePolicyOut)[]).filter((key) => policy[key] !== pending.proposed_policy[key])
    : []
  const recentDecided = changes.filter((c) => c.approval_status !== 'pending_approval').slice(0, 5)

  return (
    <div className="mt-4 rounded-lg border border-line bg-surface p-4">
      <div className="text-sm font-semibold text-ink">Compliance policy</div>
      <p className="mt-1 text-xs text-ink-soft">
        Which checks count as a compliance failure for this organization's devices. Turning one off stops it from
        creating exceptions — the agent keeps reporting the underlying value either way.
      </p>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {pending && (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <div className="font-semibold">Pending policy change — awaiting approval from {approverNames}</div>
          {changedKeys.length > 0 && (
            <ul className="mt-1 list-disc pl-4">
              {changedKeys.map((key) => (
                <li key={key}>
                  {POLICY_LABELS[key]}: {policy[key] ? 'required' : 'not required'} → {pending.proposed_policy[key] ? 'required' : 'not required'}
                </li>
              ))}
            </ul>
          )}
          {canApprove && pending.requested_by !== user?.user_id ? (
            <div className="mt-2 flex gap-2">
              <button onClick={() => approve(pending.policy_change_id)} disabled={busyId === pending.policy_change_id} className={BTN_PRIMARY}>
                {busyId === pending.policy_change_id ? 'Approving…' : 'Approve'}
              </button>
              <button
                onClick={() => setRejectingId(pending.policy_change_id)}
                disabled={busyId === pending.policy_change_id}
                className={BTN_SECONDARY}
              >
                Reject
              </button>
            </div>
          ) : (
            <p className="mt-1 text-amber-700">
              {pending.requested_by === user?.user_id
                ? "You submitted this change — a different authorized approver must approve or reject it."
                : 'Awaiting review by an authorized approver.'}
            </p>
          )}
        </div>
      )}

      <div className="mt-3 space-y-3">
        {(Object.keys(POLICY_LABELS) as (keyof DevicePolicyOut)[]).map((key) => (
          <label key={key} className={`flex items-start gap-2 text-sm text-ink ${pending ? 'opacity-60' : ''}`}>
            <input
              type="checkbox"
              className="mt-0.5"
              checked={draft[key]}
              onChange={() => toggleDraft(key)}
              disabled={submitting || pending !== null}
            />
            <span>
              <span className="block">{POLICY_LABELS[key]}</span>
              <span className="block text-xs font-normal text-ink-soft">{POLICY_DESCRIPTIONS[key]}</span>
            </span>
          </label>
        ))}
      </div>

      {!pending && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button onClick={submit} disabled={!hasDraftChanges || submitting} className={BTN_PRIMARY}>
            {submitting ? 'Submitting…' : 'Submit for approval'}
          </button>
          {hasDraftChanges && (
            <>
              <button onClick={() => setDraft(policy)} disabled={submitting} className={BTN_SECONDARY}>
                Discard changes
              </button>
              <span className="text-xs text-ink-soft">
                Goes to {approverNames} for approval — you won't be able to approve your own change. Approving
                immediately re-evaluates every enrolled device's latest known state, so any devices already failing a
                newly-required check show up as exceptions right away.
              </span>
            </>
          )}
        </div>
      )}

      {recentDecided.length > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Recent decisions</div>
          <div className="mt-1 space-y-1">
            {recentDecided.map((change) => (
              <div key={change.policy_change_id} className="text-xs text-ink-soft">
                {new Date(change.requested_at).toLocaleString()} —{' '}
                <span className={change.approval_status === 'approved' ? 'font-medium text-accent-ink' : 'font-medium text-red-600'}>
                  {APPROVAL_STATUS_LABELS[change.approval_status] ?? change.approval_status}
                </span>
                {change.approval_status === 'rejected' && change.rejected_reason ? `: ${change.rejected_reason}` : ''}
              </div>
            ))}
          </div>
        </div>
      )}

      <ConfirmDialog
        open={rejectingId !== null}
        title="Reject policy change"
        message="Reject this compliance policy change? It will never take effect — the person who submitted it can see why and resubmit if appropriate."
        confirmLabel="Reject"
        reasonRequired
        reasonValue={rejectReason}
        onReasonChange={setRejectReason}
        reasonPlaceholder="Why is this policy change being rejected?"
        onConfirm={reject}
        onCancel={() => {
          setRejectingId(null)
          setRejectReason('')
        }}
      />
    </div>
  )
}

const RISK_STYLES: Record<string, string> = {
  low: 'bg-bg text-ink-soft',
  medium: 'bg-amber-50 text-amber-700',
  high: 'bg-orange-50 text-orange-700',
  critical: 'bg-red-50 text-red-700',
}

const CLASSIFICATION_LABELS: Record<PolicyClassification, string> = {
  approved: 'Approved',
  required: 'Required',
  restricted: 'Restricted',
  system_component: 'System component',
  ignored: 'Ignored',
  review_required: 'Review required',
}

const CLASSIFICATION_STYLES: Record<string, string> = {
  approved: 'bg-accent-soft text-accent-ink',
  required: 'bg-blue-50 text-blue-700',
  restricted: 'bg-red-50 text-red-700',
  system_component: 'bg-bg text-ink-soft',
  ignored: 'bg-bg text-ink-soft',
  review_required: 'bg-amber-50 text-amber-700',
  unknown: 'bg-bg text-ink-soft',
}

const CLASSIFICATION_HELP: Record<PolicyClassification, string> = {
  approved: 'Present = compliant. Absent has no effect.',
  required: 'Must be installed — absent creates an exception.',
  restricted: 'Must not be installed — present creates an exception.',
  system_component: 'Expected OS/runtime component — never flagged.',
  ignored: 'Not evaluated for compliance at all.',
  review_required: "Flagged for an admin's attention, but not an exception.",
}

const APPROVAL_STATUS_LABELS: Record<ApprovalStatus, string> = {
  pending_approval: 'Pending approval',
  approved: 'Approved',
  rejected: 'Rejected',
  superseded: 'Superseded',
}

const APPROVAL_STATUS_STYLES: Record<ApprovalStatus, string> = {
  pending_approval: 'bg-amber-50 text-amber-700',
  approved: 'bg-accent-soft text-accent-ink',
  rejected: 'bg-red-50 text-red-700',
  superseded: 'bg-bg text-ink-faint',
}

function ApprovedSoftwarePanel({ organizationId }: { organizationId: string }) {
  const { user } = useAuth()
  const [entries, setEntries] = useState<ApprovedSoftwareOut[]>([])
  const [form, setForm] = useState({ app_name: '', publisher: '', classification: 'approved' as PolicyClassification, risk_level: 'medium' as RiskLevel })
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [rejectingId, setRejectingId] = useState<string | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  const [showSuperseded, setShowSuperseded] = useState(false)

  const load = () => apiClient.get<ApprovedSoftwareOut[]>(`/organizations/${organizationId}/approved-software`).then((res) => setEntries(res.data))

  useEffect(() => {
    load()
  }, [organizationId])

  const add = async (e: FormEvent) => {
    e.preventDefault()
    setAdding(true)
    setError(null)
    try {
      await apiClient.post(`/organizations/${organizationId}/approved-software`, {
        app_name: form.app_name,
        publisher: form.publisher || null,
        classification: form.classification,
        risk_level: form.risk_level,
      })
      setForm({ app_name: '', publisher: '', classification: 'approved', risk_level: 'medium' })
      load()
    } catch {
      setError('Could not add this entry.')
    } finally {
      setAdding(false)
    }
  }

  const approve = async (id: string) => {
    setBusyId(id)
    setError(null)
    try {
      await apiClient.post(`/organizations/${organizationId}/approved-software/${id}/approve`)
      load()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not approve this classification.')
    } finally {
      setBusyId(null)
    }
  }

  const reject = async () => {
    if (!rejectingId) return
    const id = rejectingId
    setBusyId(id)
    setError(null)
    try {
      await apiClient.post(`/organizations/${organizationId}/approved-software/${id}/reject`, { reason: rejectReason })
      setRejectingId(null)
      setRejectReason('')
      load()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not reject this classification.')
    } finally {
      setBusyId(null)
    }
  }

  const [removingId, setRemovingId] = useState<string | null>(null)
  const doRemove = async () => {
    if (!removingId) return
    const id = removingId
    setRemovingId(null)
    await apiClient.delete(`/organizations/${organizationId}/approved-software/${id}`)
    load()
  }

  const visibleEntries = entries.filter((e) => showSuperseded || e.approval_status !== 'superseded')

  return (
    <div className="mt-4 rounded-lg border border-line bg-surface p-4">
      <div className="text-sm font-semibold text-ink">Software policy (AS-004)</div>
      <p className="mt-1 text-xs text-ink-soft">
        Only <strong>Restricted</strong> software present, or <strong>Required</strong> software missing, ever creates
        an exception. Everything else you install without a policy row shows as "Unknown, review required" in the
        device's inventory but is never flagged as non-compliant on its own. A classification only takes effect once
        approved by a different, authorized user — the person who classified it can never approve or reject it themselves.
      </p>
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {visibleEntries.length > 0 && (
        <div className="mt-3 space-y-1">
          {visibleEntries.map((entry) => (
            <div key={entry.approved_software_id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line px-2 py-1.5 text-sm">
              <span className="text-ink">
                {entry.app_name}
                {entry.publisher && <span className="text-ink-soft"> · {entry.publisher}</span>}
                {entry.version > 1 && <span className="text-ink-faint"> · v{entry.version}</span>}
                {entry.approval_status === 'rejected' && entry.rejected_reason && (
                  <span className="block text-xs text-red-700">Rejected: {entry.rejected_reason}</span>
                )}
              </span>
              <div className="flex items-center gap-2">
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${CLASSIFICATION_STYLES[entry.classification] ?? ''}`}>
                  {CLASSIFICATION_LABELS[entry.classification as PolicyClassification] ?? entry.classification}
                </span>
                {entry.classification === 'restricted' && (
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${RISK_STYLES[entry.risk_level] ?? ''}`}>{entry.risk_level}</span>
                )}
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${APPROVAL_STATUS_STYLES[entry.approval_status]}`}>
                  {APPROVAL_STATUS_LABELS[entry.approval_status]}
                </span>
                {entry.approval_status === 'pending_approval' &&
                  (entry.created_by !== user?.user_id ? (
                    <>
                      <button onClick={() => approve(entry.approved_software_id)} disabled={busyId === entry.approved_software_id} className={BTN_PRIMARY}>
                        Approve
                      </button>
                      <button onClick={() => setRejectingId(entry.approved_software_id)} disabled={busyId === entry.approved_software_id} className={BTN_SECONDARY}>
                        Reject
                      </button>
                    </>
                  ) : (
                    <span className="text-[11px] text-ink-faint">You submitted this — a different approver must decide it.</span>
                  ))}
                {entry.approval_status !== 'superseded' && (
                  <button onClick={() => setRemovingId(entry.approved_software_id)} className="text-xs font-medium text-red-600 hover:underline">
                    Remove
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      {entries.some((e) => e.approval_status === 'superseded') && (
        <button onClick={() => setShowSuperseded((v) => !v)} className="mt-2 text-xs font-medium text-ink-soft hover:underline">
          {showSuperseded ? 'Hide superseded versions' : 'Show superseded versions'}
        </button>
      )}
      <ConfirmDialog
        open={removingId !== null}
        title="Remove approved software"
        message="Remove this app from the approved list? Devices with it installed may start showing an exception."
        confirmLabel="Remove"
        danger
        onConfirm={doRemove}
        onCancel={() => setRemovingId(null)}
      />
      <ConfirmDialog
        open={rejectingId !== null}
        title="Reject classification"
        message="Reject this classification? It will never take effect — the person who submitted it can see why and resubmit if appropriate."
        confirmLabel="Reject"
        reasonRequired
        reasonValue={rejectReason}
        onReasonChange={setRejectReason}
        reasonPlaceholder="Why is this classification being rejected?"
        onConfirm={reject}
        onCancel={() => {
          setRejectingId(null)
          setRejectReason('')
        }}
      />

      <form onSubmit={add} className="mt-3 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <input
            required
            placeholder="App name (must match exactly, e.g. Google Chrome)"
            value={form.app_name}
            onChange={(e) => setForm({ ...form, app_name: e.target.value })}
            className="flex-1 rounded-md border border-line px-2 py-1 text-sm"
          />
          <input
            placeholder="Publisher (optional)"
            value={form.publisher}
            onChange={(e) => setForm({ ...form, publisher: e.target.value })}
            className="w-40 rounded-md border border-line px-2 py-1 text-sm"
          />
          <select
            value={form.classification}
            onChange={(e) => setForm({ ...form, classification: e.target.value as PolicyClassification })}
            className="rounded-md border border-line px-2 py-1 text-sm"
          >
            {(Object.keys(CLASSIFICATION_LABELS) as PolicyClassification[]).map((c) => (
              <option key={c} value={c}>
                {CLASSIFICATION_LABELS[c]}
              </option>
            ))}
          </select>
          {form.classification === 'restricted' && (
            <select
              value={form.risk_level}
              onChange={(e) => setForm({ ...form, risk_level: e.target.value as RiskLevel })}
              className="rounded-md border border-line px-2 py-1 text-sm"
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="critical">Critical</option>
            </select>
          )}
          <button type="submit" disabled={adding} className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60">
            {adding ? 'Adding…' : 'Add'}
          </button>
        </div>
        <p className="text-xs text-ink-soft">{CLASSIFICATION_HELP[form.classification]}</p>
      </form>
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  )
}

function DeletedDevicesHistory({ organizationId }: { organizationId: string }) {
  const [deleted, setDeleted] = useState<DeviceOut[] | null>(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    apiClient.get<DeviceOut[]>(`/organizations/${organizationId}/devices/deleted`).then((res) => setDeleted(res.data))
  }, [organizationId])

  if (deleted === null || deleted.length === 0) return null

  return (
    <div className="mt-6 rounded-lg border border-line bg-surface">
      <button onClick={() => setExpanded((v) => !v)} className="flex w-full items-center justify-between px-4 py-3 text-left">
        <span className="text-sm font-semibold text-ink">Deleted devices ({deleted.length})</span>
        <span className="text-xs text-ink-soft">{expanded ? 'Hide ▴' : 'Show ▾'}</span>
      </button>
      {expanded && (
        <div className="overflow-x-auto border-t border-line">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
                <th className="px-4 py-2">Device</th>
                <th className="px-4 py-2">Reason</th>
                <th className="px-4 py-2">Requested</th>
                <th className="px-4 py-2">Approved</th>
              </tr>
            </thead>
            <tbody>
              {deleted.map((d) => (
                <tr key={d.device_id} className="border-t border-line">
                  <td className="px-4 py-2 text-ink">
                    {d.device_name}
                    {d.hostname && <span className="text-ink-soft"> ({d.hostname})</span>}
                  </td>
                  <td className="px-4 py-2 text-ink-soft">{d.deletion_reason ?? '—'}</td>
                  <td className="px-4 py-2 text-ink-soft">{d.deletion_requested_at ? new Date(d.deletion_requested_at).toLocaleString() : '—'}</td>
                  <td className="px-4 py-2 text-ink-soft">{d.deletion_approved_at ? new Date(d.deletion_approved_at).toLocaleString() : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export function DevicesPage() {
  const { hasRole, user } = useAuth()
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [devices, setDevices] = useState<DeviceOut[]>([])
  const [name, setName] = useState('')
  const [assignedUserName, setAssignedUserName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [justCreated, setJustCreated] = useState<DeviceCreatedOut | null>(null)
  const [deletedHistoryKey, setDeletedHistoryKey] = useState(0)
  const [eligibleRevokeApprovers, setEligibleRevokeApprovers] = useState<EligibleApproverOut[]>([])
  const [eligibleDeleteApprovers, setEligibleDeleteApprovers] = useState<EligibleApproverOut[]>([])
  const [eligibleClassificationApprovers, setEligibleClassificationApprovers] = useState<EligibleApproverOut[]>([])

  // Mirrors migration 0034's role grants exactly — a maker role never also
  // holds the matching approve permission, so "who can request X" and "who
  // can approve X" are always two different lists here, not an identity
  // check layered on one shared list.
  const canEnrol = hasRole('Platform Super Admin', 'Platform Admin', 'Device Manager', 'Client IT Admin')
  const canRequestRevoke = hasRole('Platform Super Admin', 'Platform Admin', 'Device Manager', 'IT/Audit Technical User', 'Client IT Admin')
  const canApproveRevoke = hasRole('Platform Super Admin', 'Platform Admin', 'Audit Manager')
  const canRequestDelete = hasRole('Platform Super Admin', 'Platform Admin', 'Device Manager', 'Client IT Admin')
  const canApproveDelete = hasRole('Platform Super Admin') // "final purge" — deliberately the narrowest grant of all
  const canCommandSafe = hasRole('Platform Super Admin', 'Platform Admin', 'Device Manager', 'IT/Audit Technical User', 'Client IT Admin')
  const canCommandDisruptive = hasRole('Platform Super Admin', 'Platform Admin', 'Device Manager', 'Client IT Admin')
  const canManagePolicy = hasRole(...CAN_MANAGE_POLICY_ROLES)

  const load = (orgId: string) => apiClient.get<DeviceOut[]>(`/organizations/${orgId}/devices`).then((res) => setDevices(res.data))

  useEffect(() => {
    if (!organizationId) return
    load(organizationId)
    apiClient
      .get<EligibleApproverOut[]>(`/organizations/${organizationId}/devices/eligible-approvers`, { params: { action: 'revoke' } })
      .then((res) => setEligibleRevokeApprovers(res.data.filter((a) => a.user_id !== user?.user_id)))
    apiClient
      .get<EligibleApproverOut[]>(`/organizations/${organizationId}/devices/eligible-approvers`, { params: { action: 'delete' } })
      .then((res) => setEligibleDeleteApprovers(res.data.filter((a) => a.user_id !== user?.user_id)))
    apiClient
      .get<EligibleApproverOut[]>(`/organizations/${organizationId}/devices/eligible-approvers`, { params: { action: 'classify' } })
      .then((res) => setEligibleClassificationApprovers(res.data.filter((a) => a.user_id !== user?.user_id)))
  }, [organizationId])

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!organizationId) return
    setError(null)
    setIsSubmitting(true)
    try {
      const res = await apiClient.post<DeviceCreatedOut>(`/organizations/${organizationId}/devices`, {
        device_name: name,
        assigned_user_name: assignedUserName || null,
      })
      setJustCreated(res.data)
      setName('')
      setAssignedUserName('')
      load(organizationId)
    } catch {
      setError('Could not register the device.')
    } finally {
      setIsSubmitting(false)
    }
  }


  const onlineCount = devices.filter((d) => ONLINE_STATUSES.has(d.status)).length
  const nonCompliantCount = devices.filter((d) => d.compliance_status === 'non_compliant').length

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Endpoint Devices</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Managed corporate laptops enrolled with the Madire Endpoint Agent — a protected background service reporting
        antivirus, firewall, disk encryption and patch status. A device that stops checking in is itself flagged as an
        exception under "Endpoint Security Compliance," not just a monitoring gap.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      {canManagePolicy && organizationId && <DevicePolicyPanel organizationId={organizationId} />}
      {canManagePolicy && organizationId && <ApprovedSoftwarePanel organizationId={organizationId} />}

      {devices.length > 0 && (
        <div className="mt-4 flex gap-4 text-sm">
          <span className="text-ink-soft">{devices.length} device{devices.length !== 1 ? 's' : ''}</span>
          <span className="text-ink-soft">
            <span className="font-medium text-ink">{onlineCount}</span> online
          </span>
          {nonCompliantCount > 0 && (
            <span className="font-medium text-red-600">{nonCompliantCount} non-compliant</span>
          )}
        </div>
      )}

      <div className="mt-4 flex gap-2">
        <a
          href={`${API_BASE_URL}/endpoint-agent/download/windows`}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white"
        >
          Download for Windows
        </a>
      </div>
      <p className="mt-1 text-xs text-ink-soft">
        Windows only — a single application file, nothing to unzip. Linux/Unix servers are monitored via the Gateway instead.
      </p>

      {justCreated && (
        <div className="mt-4 rounded-lg border border-accent-soft bg-accent-soft p-4 text-sm">
          <div className="font-semibold text-accent-ink">Registration code — single use, expires in 15 minutes</div>
          <div className="mt-2 font-mono text-lg tracking-wider text-ink">{justCreated.registration_code}</div>
          <p className="mt-2 text-ink-soft">
            On the laptop: save <code>MadireEndpointAgent.exe</code> to a permanent folder and double-click it. It opens
            a setup window with the platform already filled in — just enter this code and click{' '}
            <strong>Register &amp; Install Service</strong>. A permission prompt appears only for that one step
            (needed so the service can read BitLocker status).
          </p>
          <button onClick={() => setJustCreated(null)} className="mt-2 text-xs font-medium text-accent-ink hover:underline">
            Dismiss
          </button>
        </div>
      )}

      <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Device</th>
              <th className="px-4 py-2">User</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Agent</th>
              <th className="px-4 py-2">Security</th>
              <th className="px-4 py-2">Last check-in</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {devices.map((d) => (
              <DeviceRow
                key={d.device_id}
                device={d}
                canEnrol={canEnrol}
                canRequestRevoke={canRequestRevoke}
                canApproveRevoke={canApproveRevoke}
                canRequestDelete={canRequestDelete}
                canApproveDelete={canApproveDelete}
                canCommandSafe={canCommandSafe}
                canCommandDisruptive={canCommandDisruptive}
                canManagePolicy={canManagePolicy}
                currentUserId={user?.user_id}
                eligibleRevokeApprovers={eligibleRevokeApprovers}
                eligibleDeleteApprovers={eligibleDeleteApprovers}
                eligibleClassificationApprovers={eligibleClassificationApprovers}
                onRevoked={() => {
                  if (organizationId) load(organizationId)
                  setDeletedHistoryKey((k) => k + 1)
                }}
              />
            ))}
            {devices.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-ink-soft">
                  No devices enrolled yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {canEnrol && organizationId && (
        <form onSubmit={handleSubmit} className="mt-6 max-w-md rounded-lg border border-line bg-surface p-5">
          <h2 className="text-sm font-semibold text-ink">Enroll a new device</h2>
          <div className="mt-4 space-y-3">
            <input
              required
              placeholder="Device name (e.g. Finance Laptop 07)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            />
            <input
              placeholder="Assigned user (optional, e.g. T. Naidoo)"
              value={assignedUserName}
              onChange={(e) => setAssignedUserName(e.target.value)}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            />
          </div>
          {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={isSubmitting}
            className="mt-4 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {isSubmitting ? 'Generating…' : 'Generate registration code'}
          </button>
        </form>
      )}

      {organizationId && <DeletedDevicesHistory key={`${organizationId}-${deletedHistoryKey}`} organizationId={organizationId} />}
    </div>
  )
}
