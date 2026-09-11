import { Fragment, useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { AUDIT_FRAMEWORK_ROLES } from '../auth/permissions'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { ExceptionOut, ExceptionRecordOut } from '../types/api'

const SEVERITY_STYLES: Record<string, string> = {
  critical: 'bg-red-50 text-red-700',
  high: 'bg-orange-50 text-orange-700',
  medium: 'bg-bg text-ink-soft',
  low: 'bg-accent-soft text-accent-ink',
}

const STATUS_OPTIONS = ['open', 'awaiting_evidence', 'in_progress', 'resolved', 'closed']

// exception_data is a free-form JSONB blob — its shape differs by which
// test produced it (an endpoint check, a software policy check, or a
// SQL-backed audit rule with its own column names). Rather than assume one
// shape, every key is rendered as a plain-English label, with a few known
// keys given a nicer name/value — an unrecognized key still renders
// correctly, just in Title Case instead of raw snake_case.
const EXCEPTION_FIELD_LABELS: Record<string, string> = {
  device_id: 'Device',
  hostname: 'Device hostname',
  check: 'Compliance check',
  value: 'Result',
  app_name: 'Application',
}

const COMPLIANCE_CHECK_NAMES: Record<string, string> = {
  antivirus_enabled: 'Antivirus / real-time protection',
  firewall_enabled: 'Firewall',
  disk_encryption_enabled: 'Disk encryption (BitLocker)',
  os_up_to_date: 'Operating system updates',
}

function titleCaseFieldName(key: string): string {
  return key
    .replace(/_/g, ' ')
    .split(' ')
    .filter(Boolean)
    .map((word) => (word.toLowerCase() === 'id' ? 'ID' : word.charAt(0).toUpperCase() + word.slice(1)))
    .join(' ')
}

function humanizeExceptionValue(key: string, value: unknown, data: Record<string, unknown>): string {
  if (key === 'check' && typeof value === 'string') return COMPLIANCE_CHECK_NAMES[value] ?? titleCaseFieldName(value)
  if (key === 'value' && typeof data.check === 'string') return value === false ? 'Failing' : value === true ? 'Passing' : String(value)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (value === null || value === undefined || value === '') return 'Not reported'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function ExceptionRow({ exception, canManage, onChanged }: { exception: ExceptionOut; canManage: boolean; onChanged: () => void }) {
  const [open, setOpen] = useState(false)
  const [records, setRecords] = useState<ExceptionRecordOut[] | null>(null)
  const [escalating, setEscalating] = useState(false)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [riskRating, setRiskRating] = useState('high')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [justCreatedTitle, setJustCreatedTitle] = useState<string | null>(null)

  const toggle = () => {
    if (!open && records === null) {
      apiClient.get<ExceptionRecordOut[]>(`/exceptions/${exception.exception_id}/records`).then((res) => setRecords(res.data))
    }
    setOpen(!open)
  }

  const changeStatus = async (status: string) => {
    await apiClient.patch(`/exceptions/${exception.exception_id}`, { status })
    onChanged()
  }

  const startEscalating = () => {
    // Pre-fill from the exception itself so the common case is a single
    // click + review, not retyping what's already on the row — both fields
    // stay in normal editable inputs, so an auditor can still change either
    // before submitting.
    if (!escalating) {
      setTitle(exception.exception_description ?? '')
      setRiskRating(exception.severity ?? 'medium')
    }
    setEscalating(!escalating)
  }

  const escalate = async () => {
    setIsSubmitting(true)
    setError(null)
    try {
      await apiClient.post(`/exceptions/${exception.exception_id}/findings`, {
        finding_title: title,
        finding_description: description || null,
        risk_rating: riskRating,
      })
      setJustCreatedTitle(title)
      setEscalating(false)
      setTitle('')
      setDescription('')
      onChanged()
      setTimeout(() => setJustCreatedTitle(null), 5000)
    } catch (err: any) {
      const detail = err?.response?.data?.detail
      if (err?.response?.status === 403) {
        setError("You don't have permission to create findings — this requires an Audit Manager, Auditor, or similar role.")
      } else {
        setError(typeof detail === 'string' ? detail : 'Could not create the finding. Please try again.')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Fragment>
      <tr className="border-t border-line">
        <td className="px-4 py-2">
          <button onClick={toggle} className="font-mono text-xs text-accent-ink hover:underline">
            {exception.exception_id.slice(0, 8)}
          </button>
        </td>
        <td className="px-4 py-2 text-sm text-ink">{exception.exception_description ?? '—'}</td>
        <td className="px-4 py-2">
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${SEVERITY_STYLES[exception.severity ?? ''] ?? ''}`}>
            {exception.severity ?? '—'}
          </span>
        </td>
        <td className="px-4 py-2">
          {canManage ? (
            <select
              value={exception.status}
              onChange={(e) => changeStatus(e.target.value)}
              className="rounded-md border border-line px-2 py-1 text-xs"
            >
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          ) : (
            <span className="text-xs text-ink-soft">{exception.status}</span>
          )}
        </td>
        <td className="px-4 py-2 text-xs text-ink-soft">
          <div>{new Date(exception.last_detected_at).toLocaleString()}</div>
          {exception.occurrence_count > 1 && (
            <div className="mt-0.5 inline-block rounded-full bg-bg px-2 py-0.5 text-[11px] font-medium text-ink-soft">
              seen {exception.occurrence_count}× · first {new Date(exception.detected_at).toLocaleDateString()}
            </div>
          )}
        </td>
        <td className="px-4 py-2">
          {justCreatedTitle ? (
            <span className="text-xs font-medium text-accent-ink">✓ Finding "{justCreatedTitle}" created</span>
          ) : exception.has_finding ? (
            <span className="text-xs font-medium text-ink-soft">✓ Finding created</span>
          ) : (
            canManage && (
              <button onClick={startEscalating} className="text-xs font-medium text-accent-ink hover:underline">
                Escalate to finding
              </button>
            )
          )}
        </td>
      </tr>
      {escalating && (
        <tr className="border-t border-line bg-bg">
          <td colSpan={6} className="px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <input placeholder="Finding title" value={title} onChange={(e) => setTitle(e.target.value)} className="flex-1 rounded-md border border-line px-2 py-1 text-xs" />
              <select value={riskRating} onChange={(e) => setRiskRating(e.target.value)} className="rounded-md border border-line px-2 py-1 text-xs">
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </select>
              <button
                onClick={escalate}
                disabled={!title || isSubmitting}
                className="rounded-md bg-accent px-3 py-1 text-xs font-medium text-white disabled:opacity-60"
              >
                {isSubmitting ? 'Creating…' : 'Create finding'}
              </button>
            </div>
            <textarea
              placeholder="Description (optional)"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="mt-2 w-full rounded-md border border-line px-2 py-1 text-xs"
              rows={2}
            />
            {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
          </td>
        </tr>
      )}
      {open && records && (
        <tr className="border-t border-line bg-bg">
          <td colSpan={6} className="px-4 py-3">
            {exception.recommended_remediation && (
              <div className="mb-3 rounded-md border border-accent-soft bg-accent-soft/40 p-2">
                <div className="text-xs font-medium uppercase tracking-wide text-accent-ink">How to resolve this</div>
                <p className="mt-1 text-xs text-ink">{exception.recommended_remediation}</p>
              </div>
            )}
            <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Exception record{records.length !== 1 ? 's' : ''}</div>
            {records.map((r) => (
              <dl key={r.exception_record_id} className="mt-2 grid grid-cols-[max-content,1fr] gap-x-4 gap-y-1 rounded-md bg-surface p-3 text-sm">
                {Object.entries(r.exception_data ?? {}).map(([key, value]) => (
                  <Fragment key={key}>
                    <dt className="text-ink-soft">{EXCEPTION_FIELD_LABELS[key] ?? titleCaseFieldName(key)}</dt>
                    <dd className="text-ink">{humanizeExceptionValue(key, value, r.exception_data ?? {})}</dd>
                  </Fragment>
                ))}
                {(!r.exception_data || Object.keys(r.exception_data).length === 0) && (
                  <dd className="text-ink-soft">No additional detail recorded.</dd>
                )}
              </dl>
            ))}
          </td>
        </tr>
      )}
    </Fragment>
  )
}

export function ExceptionsPage() {
  const { hasRole } = useAuth()
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [exceptions, setExceptions] = useState<ExceptionOut[]>([])
  // "Active" hides resolved/closed ones — an exception that's been re-tested
  // and confirmed fixed (or manually resolved) shouldn't linger in the
  // working list. "History" reveals every status plus a date range — nothing
  // is ever deleted, so the full record is always there to filter into.
  const [view, setView] = useState<'active' | 'history'>('active')
  const [statusFilter, setStatusFilter] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')

  const canManage = hasRole(...AUDIT_FRAMEWORK_ROLES)

  const load = (orgId: string) => apiClient.get<ExceptionOut[]>(`/organizations/${orgId}/exceptions`).then((res) => setExceptions(res.data))

  useEffect(() => {
    if (organizationId) load(organizationId)
  }, [organizationId])

  const visible = exceptions.filter((e) => {
    if (view === 'active') return e.status !== 'resolved' && e.status !== 'closed'
    if (statusFilter && e.status !== statusFilter) return false
    const detected = new Date(e.last_detected_at)
    if (fromDate && detected < new Date(fromDate)) return false
    if (toDate && detected > new Date(`${toDate}T23:59:59`)) return false
    return true
  })

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Exceptions</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Individual failed conditions from a test execution — not yet a finding. An auditor investigates and decides
        whether to escalate.
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
              {STATUS_OPTIONS.map((s) => (
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
        <span className="text-xs text-ink-soft">{visible.length} shown</span>
      </div>

      <div className="mt-3 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">ID</th>
              <th className="px-4 py-2">Description</th>
              <th className="px-4 py-2">Severity</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Last detected</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {visible.map((e) => (
              <ExceptionRow
                key={e.exception_id}
                exception={e}
                canManage={canManage}
                onChanged={() => organizationId && load(organizationId)}
              />
            ))}
            {visible.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-ink-soft">
                  No exceptions.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
