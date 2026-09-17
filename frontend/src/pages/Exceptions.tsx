import { Fragment, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { AUDIT_FRAMEWORK_ROLES } from '../auth/permissions'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import { ExceptionExplanationBlock } from '../components/ExceptionExplanation'
import type { EvidenceRequestOut, ExceptionCommentOut, ExceptionExplanationOut, ExceptionOut, ExceptionRecordOut, ExceptionTraceOut, UserOut } from '../types/api'

// "T. Naidoo (Client IT Admin)" — showing the role alongside the name
// makes it clear, at a glance, who's the auditor and who's the client
// responding, in a thread/log where both sides post.
function userLabel(orgUsers: UserOut[], userId: string | null): string | null {
  const u = orgUsers.find((x) => x.user_id === userId)
  if (!u) return null
  return u.roles.length > 0 ? `${u.first_name} ${u.last_name} (${u.roles[0]})` : `${u.first_name} ${u.last_name}`
}

// "T. Naidoo (Client IT Admin)" built directly from a name+role the
// backend already resolved — used for comments/evidence requests, where
// the poster can be an internal auditor who'd never appear in this
// client organization's own /organizations/{id}/users list (see
// routes/exceptions.py._resolve_user_display).
function labelFor(name: string | null, role: string | null): string | null {
  if (!name) return null
  return role ? `${name} (${role})` : name
}

function AvatarCircle({ initials, internal }: { initials: string; internal: boolean }) {
  return (
    <span
      className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold text-white ${
        internal ? 'bg-ink' : 'bg-teal-600'
      }`}
    >
      {initials}
    </span>
  )
}

function AvatarFor({ name, role }: { name: string | null; role: string | null }) {
  const initials = name
    ? name
        .trim()
        .split(/\s+/)
        .slice(0, 2)
        .map((p) => p[0]?.toUpperCase() ?? '')
        .join('') || '?'
    : '?'
  const internal = role ? (AUDIT_FRAMEWORK_ROLES as readonly string[]).includes(role) : false
  return <AvatarCircle initials={initials} internal={internal} />
}

// A small file-type icon ("PDF", "XLS", "DOC", "?" while still awaiting)
// — the same visual shorthand the product spec's mockup uses, so a list
// of requests reads at a glance instead of as plain text. Drawn as an
// actual document glyph (folded corner) rather than a flat colored
// square, so it reads as a file icon instead of a label chip.
function fileTypeBadge(fileName: string | null): { label: string; textClass: string } {
  const ext = fileName?.split('.').pop()?.toUpperCase() ?? ''
  if (ext === 'PDF') return { label: 'PDF', textClass: 'text-red-600' }
  if (ext === 'XLS' || ext === 'XLSX') return { label: 'XLS', textClass: 'text-emerald-600' }
  if (ext === 'CSV') return { label: 'CSV', textClass: 'text-emerald-600' }
  if (ext === 'DOC' || ext === 'DOCX') return { label: 'DOC', textClass: 'text-blue-600' }
  if (ext) return { label: ext.slice(0, 3), textClass: 'text-ink-soft' }
  return { label: '', textClass: 'text-ink-soft' }
}

function FileBadge({ fileName }: { fileName: string | null }) {
  const { label, textClass } = fileTypeBadge(fileName)
  return (
    <span className={`relative flex h-9 w-8 shrink-0 items-start justify-center ${textClass}`}>
      <svg viewBox="0 0 24 28" className="h-9 w-8" fill="none" aria-hidden="true">
        <path
          d="M3.5 2.5c0-.55.45-1 1-1H14l6.5 6.5V25.5c0 .55-.45 1-1 1h-15c-.55 0-1-.45-1-1V2.5z"
          fill="currentColor"
          fillOpacity="0.1"
          stroke="currentColor"
          strokeWidth="1.25"
          strokeLinejoin="round"
        />
        <path d="M14 1.5V7c0 .55.45 1 1 1h5.5" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinejoin="round" />
      </svg>
      {label && <span className="absolute bottom-1.5 text-[7px] font-bold tracking-tight">{label}</span>}
    </span>
  )
}

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

const ISO_TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/
// Matches a lowercase status/enum-style value ("active", "procurement_
// officer") — never an identifier like "U008" or "EMP008" (uppercase
// letters) or a username like "d.pretorius" (a period isn't in this
// character class), both of which are meant to stay exactly as-is.
const SNAKE_CASE_WORD_PATTERN = /^[a-z0-9]+(_[a-z0-9]+)*$/

// A list value can arrive two ways depending on how the source system
// stored it: a real JSON/JS array, or (some raw source columns) a plain
// string holding a Python-style list literal such as
// "['procurement_officer']" — single-quoted, not valid JSON as-is.
// Both are turned into the same plain, comma-separated, title-cased list
// rather than shown as array/Python syntax.
function asListOfStrings(value: unknown): string[] | null {
  if (Array.isArray(value)) return value.map(String)
  if (typeof value === 'string' && /^\[.*\]$/.test(value.trim())) {
    try {
      const parsed = JSON.parse(value.replace(/'/g, '"'))
      if (Array.isArray(parsed)) return parsed.map(String)
    } catch {
      // fall through — not actually a list literal, render as plain text below
    }
  }
  return null
}

function humanizeExceptionValue(key: string, value: unknown, data: Record<string, unknown>): string {
  if (key === 'check' && typeof value === 'string') return COMPLIANCE_CHECK_NAMES[value] ?? titleCaseFieldName(value)
  if (key === 'value' && typeof data.check === 'string') return value === false ? 'Failing' : value === true ? 'Passing' : String(value)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (value === null || value === undefined || value === '') return 'Not reported'

  const list = asListOfStrings(value)
  if (list) return list.length > 0 ? list.map(titleCaseFieldName).join(', ') : 'None'

  if (typeof value === 'string' && ISO_TIMESTAMP_PATTERN.test(value)) {
    const parsed = new Date(value)
    if (!isNaN(parsed.getTime())) return parsed.toLocaleString()
  }

  if (typeof value === 'object') return JSON.stringify(value)
  if (typeof value === 'string' && SNAKE_CASE_WORD_PATTERN.test(value)) return titleCaseFieldName(value)
  return String(value)
}

function ExceptionRow({
  exception,
  canManage,
  canAssign,
  orgUsers,
  onChanged,
}: {
  exception: ExceptionOut
  canManage: boolean
  canAssign: boolean
  orgUsers: UserOut[]
  onChanged: () => void
}) {
  const { user } = useAuth()
  // The exception's own assigned owner can always move its status too —
  // they're the one actually doing the work, not just the audit team or
  // the client admin who assigned it. Matches the backend's PATCH
  // /exceptions/{id} check exactly, so this control is never shown only
  // to 403 when clicked.
  const canChangeStatus = canManage || canAssign || (user !== null && user.user_id === exception.owner_id)
  const [open, setOpen] = useState(false)
  const [records, setRecords] = useState<ExceptionRecordOut[] | null>(null)
  const [explanation, setExplanation] = useState<ExceptionExplanationOut | null>(null)
  const [escalating, setEscalating] = useState(false)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [riskRating, setRiskRating] = useState('high')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [justCreatedTitle, setJustCreatedTitle] = useState<string | null>(null)
  const [assigning, setAssigning] = useState(false)

  const [evidenceRequests, setEvidenceRequests] = useState<EvidenceRequestOut[]>([])
  const [comments, setComments] = useState<ExceptionCommentOut[]>([])
  const [requestingEvidence, setRequestingEvidence] = useState(false)
  const [newRequestDescription, setNewRequestDescription] = useState('')
  const [newRequestDueDate, setNewRequestDueDate] = useState('')
  const [submittingRequest, setSubmittingRequest] = useState(false)
  const [uploadingRequestId, setUploadingRequestId] = useState<string | null>(null)
  const [newCommentBody, setNewCommentBody] = useState('')
  const [submittingComment, setSubmittingComment] = useState(false)
  // Evidence requests and comments are private to this exception's own
  // owner and the audit team — a viewer who's neither gets a 403, shown
  // here plainly rather than as a misleading "no comments yet."
  const [collaborationForbidden, setCollaborationForbidden] = useState(false)

  const [trace, setTrace] = useState<ExceptionTraceOut | null>(null)
  const [showTrace, setShowTrace] = useState(false)
  const [traceLoading, setTraceLoading] = useState(false)
  // A fast-re-detecting schedule adds one exception_records row per run —
  // showing every one piles up dozens of near-identical blocks for a
  // single ongoing issue. Only the latest (records[0], the backend
  // returns them newest-first) is current by default; the rest are one
  // click away as history, never deleted or hidden for good.
  const [showRecordHistory, setShowRecordHistory] = useState(false)

  const loadCollaboration = () => {
    apiClient
      .get<EvidenceRequestOut[]>(`/exceptions/${exception.exception_id}/evidence-requests`)
      .then((res) => setEvidenceRequests(res.data))
      .catch((err) => {
        if (err?.response?.status === 403) setCollaborationForbidden(true)
      })
    apiClient
      .get<ExceptionCommentOut[]>(`/exceptions/${exception.exception_id}/comments`)
      .then((res) => setComments(res.data))
      .catch((err) => {
        if (err?.response?.status === 403) setCollaborationForbidden(true)
      })
  }

  const ensureLoaded = () => {
    if (records === null) {
      apiClient.get<ExceptionRecordOut[]>(`/exceptions/${exception.exception_id}/records`).then((res) => setRecords(res.data))
      apiClient.get<ExceptionExplanationOut>(`/exceptions/${exception.exception_id}/explanation`).then((res) => setExplanation(res.data))
      loadCollaboration()
    }
  }

  const toggle = () => {
    if (!open) ensureLoaded()
    setOpen(!open)
  }

  const toggleTrace = () => {
    if (!open) ensureLoaded()
    if (!showTrace && trace === null) {
      setTraceLoading(true)
      apiClient
        .get<ExceptionTraceOut>(`/exceptions/${exception.exception_id}/trace`)
        .then((res) => setTrace(res.data))
        .finally(() => setTraceLoading(false))
    }
    setShowTrace(!showTrace)
    setOpen(true)
  }

  // Suggest a starting point instead of a blank field — built from the
  // exception's own plain-English summary, so the common case is a
  // single click + light edit, not typing the whole request from
  // scratch. Still a normal editable input, so the auditor reviews/
  // adjusts before submitting (same pattern as Findings.tsx's remediation
  // description prefill).
  const startRequestingEvidence = () => {
    setNewRequestDescription((prev) => prev || `Evidence needed to resolve: ${exception.summary || exception.exception_description || 'this exception'}`)
    setNewRequestDueDate((prev) => {
      if (prev) return prev
      const d = new Date()
      d.setDate(d.getDate() + 7)
      return d.toISOString().slice(0, 10)
    })
    setRequestingEvidence(true)
  }

  const submitEvidenceRequest = async () => {
    if (!newRequestDescription.trim()) return
    setSubmittingRequest(true)
    try {
      await apiClient.post(`/exceptions/${exception.exception_id}/evidence-requests`, {
        description: newRequestDescription,
        due_date: newRequestDueDate || null,
      })
      setNewRequestDescription('')
      setNewRequestDueDate('')
      setRequestingEvidence(false)
      loadCollaboration()
    } finally {
      setSubmittingRequest(false)
    }
  }

  const uploadFile = async (requestId: string, file: File) => {
    setUploadingRequestId(requestId)
    try {
      const form = new FormData()
      form.append('file', file)
      await apiClient.post(`/evidence-requests/${requestId}/upload`, form)
      loadCollaboration()
    } finally {
      setUploadingRequestId(null)
    }
  }

  const downloadFile = async (requestId: string, fileId: string, fileName: string) => {
    const res = await apiClient.get(`/evidence-requests/${requestId}/files/${fileId}`, { responseType: 'blob' })
    const url = URL.createObjectURL(res.data as Blob)
    const link = document.createElement('a')
    link.href = url
    link.download = fileName
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  const [deletingFileId, setDeletingFileId] = useState<string | null>(null)

  const deleteFile = async (requestId: string, fileId: string) => {
    setDeletingFileId(fileId)
    try {
      await apiClient.delete(`/evidence-requests/${requestId}/files/${fileId}`)
      loadCollaboration()
    } finally {
      setDeletingFileId(null)
    }
  }

  const submitComment = async () => {
    if (!newCommentBody.trim()) return
    setSubmittingComment(true)
    try {
      await apiClient.post(`/exceptions/${exception.exception_id}/comments`, { body: newCommentBody })
      setNewCommentBody('')
      loadCollaboration()
    } finally {
      setSubmittingComment(false)
    }
  }

  const changeStatus = async (status: string) => {
    await apiClient.patch(`/exceptions/${exception.exception_id}`, { status })
    onChanged()
  }

  const assignOwner = async (ownerId: string) => {
    setAssigning(true)
    try {
      await apiClient.patch(`/exceptions/${exception.exception_id}`, { owner_id: ownerId || null })
      onChanged()
    } finally {
      setAssigning(false)
    }
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
          <div>
            <button onClick={toggleTrace} className="text-[11px] font-medium text-ink-soft hover:text-ink hover:underline">
              Trace evidence
            </button>
          </div>
        </td>
        <td className="px-4 py-2 text-sm text-ink">{exception.exception_description ?? '—'}</td>
        <td className="px-4 py-2">
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${SEVERITY_STYLES[exception.severity ?? ''] ?? ''}`}>
            {exception.severity ?? '—'}
          </span>
        </td>
        <td className="px-4 py-2">
          {canChangeStatus ? (
            <>
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
              <div className="mt-0.5 text-[10px] text-ink-soft">Resolved/closed auto re-runs the test to confirm.</div>
            </>
          ) : (
            <span className="text-xs text-ink-soft">{exception.status}</span>
          )}
        </td>
        <td className="px-4 py-2">
          {canAssign ? (
            <select
              value={exception.owner_id ?? ''}
              onChange={(e) => assignOwner(e.target.value)}
              disabled={assigning}
              className="rounded-md border border-line px-2 py-1 text-xs disabled:opacity-60"
            >
              <option value="">Unassigned</option>
              {/* Only users who actually hold the Exception Owner role are
                  offered — anyone else in the org (a Read Only viewer, a
                  Control Owner, etc.) isn't who this exception should be
                  handed to, even though they're a valid org member. */}
              {orgUsers
                .filter((u) => u.roles.includes('Exception Owner'))
                .map((u) => (
                  <option key={u.user_id} value={u.user_id}>
                    {u.first_name} {u.last_name}
                  </option>
                ))}
            </select>
          ) : (
            <span className="text-xs text-ink-soft">{userLabel(orgUsers, exception.owner_id) ?? 'Unassigned'}</span>
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
          <td colSpan={7} className="px-4 py-3">
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
          <td colSpan={7} className="px-4 py-3">
            {showTrace && (
              <div className="mb-3 rounded-md border border-line bg-surface p-3">
                <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Trace evidence</div>
                {traceLoading && <p className="mt-2 text-xs text-ink-soft">Loading…</p>}
                {trace && (
                  <div className="mt-2 space-y-2 font-mono text-xs">
                    <div>
                      <span className="text-ink-soft">CONTROL</span>{' '}
                      <span className="font-semibold text-ink">
                        {trace.control_code ? `${trace.control_code} — ${trace.control_name}` : 'Manually created test'}
                      </span>
                    </div>
                    <div className="text-ink-soft">↓</div>
                    <div>
                      <span className="text-ink-soft">TEST RUN</span>{' '}
                      <span className="font-semibold text-ink">{new Date(trace.executed_at).toLocaleString()}</span>
                      {trace.rule_type && <span className="text-ink-soft"> ({trace.rule_type})</span>}
                    </div>
                    {trace.objects.map((obj, i) => (
                      <div key={i}>
                        <div className="text-ink-soft">↓</div>
                        <div>
                          <span className="text-ink-soft">{(obj.table_name ?? obj.canonical_object).toUpperCase()} TABLE</span>
                          {obj.fields.length > 0 && (
                            <span className="text-ink">
                              {' → '}
                              {obj.fields.map((f, j) => (
                                <span key={j}>
                                  {j > 0 && ', '}
                                  {f.field.toLowerCase().replace(/ /g, '_')} = <span className="font-semibold">{f.value}</span>
                                </span>
                              ))}
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                    {trace.other_fields.length > 0 && (
                      <div>
                        <div className="text-ink-soft">↓</div>
                        <div>
                          <span className="text-ink-soft">OTHER FIELDS</span>{' '}
                          <span className="text-ink">
                            {trace.other_fields.map((f, j) => (
                              <span key={j}>
                                {j > 0 && ', '}
                                {f.field.toLowerCase().replace(/ /g, '_')} = <span className="font-semibold">{f.value}</span>
                              </span>
                            ))}
                          </span>
                        </div>
                      </div>
                    )}
                    <div className="text-ink-soft">↓</div>
                    <div>
                      <span className="text-ink-soft">RESULT</span>{' '}
                      <span className="font-semibold text-red-600">
                        {trace.severity ? `${trace.severity.toUpperCase()} EXCEPTION` : 'EXCEPTION'}
                      </span>
                    </div>
                    <p className="mt-2 whitespace-normal font-sans text-ink-soft">{trace.summary}</p>
                  </div>
                )}
              </div>
            )}
            {explanation && (
              <div className="mb-3">
                <ExceptionExplanationBlock
                  summary={explanation.summary}
                  whyItMatters={explanation.why_it_matters}
                  whatToDo={explanation.what_to_do}
                />
              </div>
            )}
            <div className="flex items-center justify-between">
              <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">
                Exception record{records.length !== 1 ? 's' : ''}
              </div>
              {records.length > 1 && (
                <button onClick={() => setShowRecordHistory((v) => !v)} className="text-xs font-medium text-accent-ink hover:underline">
                  {showRecordHistory ? 'Hide history' : `Show ${records.length - 1} earlier detection${records.length - 1 === 1 ? '' : 's'} (history)`}
                </button>
              )}
            </div>
            {(showRecordHistory ? records : records.slice(0, 1)).map((r, i) => (
              <div key={r.exception_record_id}>
                {i === 0 ? (
                  <p className="mt-2 text-[11px] font-medium uppercase tracking-wide text-accent-ink">Current</p>
                ) : (
                  <p className="mt-3 text-[11px] text-ink-soft">Detected {new Date(r.detected_at).toLocaleString()}</p>
                )}
                <dl className="mt-1 grid grid-cols-[max-content,1fr] gap-x-4 gap-y-1 rounded-md bg-surface p-3 text-sm">
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
              </div>
            ))}

            {collaborationForbidden ? (
              <p className="mt-4 rounded-md border border-line bg-surface p-3 text-xs text-ink-soft">
                Evidence requests and comments here are private to this exception's assigned owner and the audit
                team — you're seeing everything else about this exception, just not this conversation.
              </p>
            ) : (
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div className="rounded-md border border-line bg-surface p-3">
                <div className="flex items-center justify-between">
                  <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Evidence requested from client</div>
                  {canManage && !requestingEvidence && (
                    <button onClick={startRequestingEvidence} className="text-xs font-medium text-accent-ink hover:underline">
                      Request evidence
                    </button>
                  )}
                </div>

                {requestingEvidence && (
                  <div className="mt-2 space-y-1 rounded-md border border-line bg-bg p-2">
                    <input
                      placeholder="What do you need? e.g. AD deprovisioning ticket for JSMITH"
                      value={newRequestDescription}
                      onChange={(e) => setNewRequestDescription(e.target.value)}
                      className="w-full rounded-md border border-line px-2 py-1 text-xs"
                    />
                    <div className="flex items-center gap-2">
                      <input
                        type="date"
                        value={newRequestDueDate}
                        onChange={(e) => setNewRequestDueDate(e.target.value)}
                        className="rounded-md border border-line px-2 py-1 text-xs"
                      />
                      <button
                        onClick={submitEvidenceRequest}
                        disabled={!newRequestDescription.trim() || submittingRequest}
                        className="rounded-md bg-accent px-2 py-1 text-xs font-medium text-white disabled:opacity-60"
                      >
                        {submittingRequest ? 'Requesting…' : 'Submit'}
                      </button>
                      <button onClick={() => setRequestingEvidence(false)} className="text-xs font-medium text-ink-soft hover:underline">
                        Cancel
                      </button>
                    </div>
                  </div>
                )}

                <ul className="mt-2 divide-y divide-line">
                  {evidenceRequests.map((r) => (
                    <li key={r.request_id} className="flex items-start gap-3 py-2 text-xs first:pt-2">
                      <FileBadge fileName={r.files[0]?.file_name ?? null} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-start justify-between gap-2">
                          <div className="font-medium text-ink">{r.description}</div>
                          <span
                            className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                              r.status === 'received' ? 'bg-accent-soft text-accent-ink' : 'bg-purple-50 text-purple-700'
                            }`}
                          >
                            {r.status === 'received' ? 'Received' : 'Awaiting'}
                          </span>
                        </div>
                        <div className="text-ink-soft">
                          Requested {new Date(r.requested_at).toLocaleDateString()}
                          {labelFor(r.requested_by_name, r.requested_by_role) && ` by ${labelFor(r.requested_by_name, r.requested_by_role)}`}
                          {r.due_date && ` · due ${r.due_date}`}
                          {r.status === 'awaiting' && ' · awaiting upload'}
                        </div>

                        {r.files.length > 0 && (
                          <ul className="mt-1 space-y-1">
                            {r.files.map((f) => (
                              <li key={f.evidence_file_id} className="flex items-center gap-2">
                                <button
                                  onClick={() => downloadFile(r.request_id, f.evidence_file_id, f.file_name)}
                                  className="font-mono text-accent-ink hover:underline"
                                >
                                  {f.file_name}
                                </button>
                                <span className="text-[10px] text-ink-soft">
                                  {labelFor(f.uploaded_by_name, f.uploaded_by_role) ? `by ${labelFor(f.uploaded_by_name, f.uploaded_by_role)} · ` : ''}
                                  {new Date(f.uploaded_at).toLocaleString()}
                                </span>
                                {!canManage && (
                                  <button
                                    onClick={() => deleteFile(r.request_id, f.evidence_file_id)}
                                    disabled={deletingFileId === f.evidence_file_id}
                                    className="text-[10px] font-medium text-red-600 hover:underline disabled:opacity-60"
                                  >
                                    {deletingFileId === f.evidence_file_id ? 'Removing…' : 'Remove'}
                                  </button>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}

                        {canManage ? (
                          r.files.length === 0 && (
                            <div className="mt-1 text-[11px] text-ink-soft">Awaiting the client to upload supporting evidence.</div>
                          )
                        ) : (
                          <label className="mt-1 inline-block cursor-pointer rounded-md border border-line px-2 py-1 text-[11px] font-medium text-ink hover:bg-bg">
                            {uploadingRequestId === r.request_id ? 'Uploading…' : r.files.length > 0 ? 'Add another file' : 'Upload file'}
                            <input
                              type="file"
                              className="hidden"
                              disabled={uploadingRequestId === r.request_id}
                              onChange={(e) => {
                                const file = e.target.files?.[0]
                                if (file) uploadFile(r.request_id, file)
                                e.target.value = ''
                              }}
                            />
                          </label>
                        )}
                      </div>
                    </li>
                  ))}
                  {evidenceRequests.length === 0 && !requestingEvidence && <li className="py-2 text-xs text-ink-soft">No evidence requested yet.</li>}
                </ul>
              </div>

              <div className="rounded-md border border-line bg-surface p-3">
                <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Comments</div>
                <ul className="mt-2 space-y-3">
                  {comments.map((c) => (
                    <li key={c.comment_id} className="flex items-start gap-2 text-xs">
                      <AvatarFor name={c.author_name} role={c.author_role} />
                      <div className="min-w-0 flex-1 rounded-md bg-bg p-2">
                        <div className="font-medium text-ink">
                          {labelFor(c.author_name, c.author_role) ?? 'Unknown user'}{' '}
                          <span className="font-normal text-ink-soft">{new Date(c.created_at).toLocaleString()}</span>
                        </div>
                        <div className="mt-0.5 text-ink">{c.body}</div>
                      </div>
                    </li>
                  ))}
                  {comments.length === 0 && <li className="text-xs text-ink-soft">No comments yet.</li>}
                </ul>
                <div className="mt-2 flex gap-2">
                  <textarea
                    placeholder="Add a comment…"
                    value={newCommentBody}
                    onChange={(e) => setNewCommentBody(e.target.value)}
                    className="flex-1 rounded-md border border-line px-2 py-1 text-xs"
                    rows={2}
                  />
                  <button
                    onClick={submitComment}
                    disabled={!newCommentBody.trim() || submittingComment}
                    className="self-end rounded-md bg-accent px-3 py-1 text-xs font-medium text-white disabled:opacity-60"
                  >
                    {submittingComment ? '…' : 'Post'}
                  </button>
                </div>
              </div>
            </div>
            )}
          </td>
        </tr>
      )}
    </Fragment>
  )
}

export function ExceptionsPage() {
  const { hasRole } = useAuth()
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [searchParams, setSearchParams] = useSearchParams()
  const [exceptions, setExceptions] = useState<ExceptionOut[]>([])
  // Starts true, not false — without this, the table below has no way to
  // tell "still loading" apart from "genuinely zero exceptions," and would
  // flash "No exceptions" on every page load until the fetch resolves
  // (worse on a cold Render instance, where that first request can take
  // real seconds).
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  // "Active" hides resolved/closed ones — an exception that's been re-tested
  // and confirmed fixed (or manually resolved) shouldn't linger in the
  // working list. "History" reveals every status plus a date range — nothing
  // is ever deleted, so the full record is always there to filter into.
  const [view, setView] = useState<'active' | 'history'>('active')
  const [statusFilter, setStatusFilter] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  // Arriving from the Dashboard's "High-Risk Exceptions" tile
  // (/exceptions?risk=high) narrows straight to what that tile counted.
  const [highRiskOnly, setHighRiskOnly] = useState(searchParams.get('risk') === 'high')

  const canManage = hasRole(...AUDIT_FRAMEWORK_ROLES)
  // Deciding who owns an exception is the client organization's own call,
  // not the internal audit team's — Client Organisation Admin only (see
  // backend migration 0063's exceptions:assign, and the PATCH /exceptions
  // route, which now rejects an owner_id change from anyone else even if
  // they hold audit_framework:manage).
  const canAssign = hasRole('Client Organisation Admin')
  const [orgUsers, setOrgUsers] = useState<UserOut[]>([])

  const load = (orgId: string) => {
    setLoading(true)
    setLoadError(false)
    apiClient
      .get<ExceptionOut[]>(`/organizations/${orgId}/exceptions`)
      .then((res) => setExceptions(res.data))
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false))
    apiClient.get<UserOut[]>(`/organizations/${orgId}/users`).then((res) => setOrgUsers(res.data))
  }

  useEffect(() => {
    if (organizationId) load(organizationId)
  }, [organizationId])

  const visible = exceptions.filter((e) => {
    if (highRiskOnly && e.severity !== 'critical' && e.severity !== 'high') return false
    if (view === 'active') return e.status !== 'resolved' && e.status !== 'closed'
    if (statusFilter && e.status !== statusFilter) return false
    const detected = new Date(e.last_detected_at)
    if (fromDate && detected < new Date(fromDate)) return false
    if (toDate && detected > new Date(`${toDate}T23:59:59`)) return false
    return true
  })

  const clearHighRiskOnly = () => {
    setHighRiskOnly(false)
    const next = new URLSearchParams(searchParams)
    next.delete('risk')
    setSearchParams(next, { replace: true })
  }

  // Grouped by control so an auditor sees every open issue for ONE control
  // together, instead of hunting through one flat list — "Uncategorized"
  // is a real, rare case (a manually-created audit test with no control
  // library link), surfaced honestly rather than hidden.
  const groups = (() => {
    const byLabel = new Map<string, { label: string; code: string; items: ExceptionOut[] }>()
    for (const e of visible) {
      const label = e.control_code ? `${e.control_code} — ${e.control_name}` : 'Uncategorized'
      const key = e.control_code ?? '￿' // sorts after every real code
      if (!byLabel.has(key)) byLabel.set(key, { label, code: key, items: [] })
      byLabel.get(key)!.items.push(e)
    }
    return [...byLabel.values()].sort((a, b) => a.code.localeCompare(b.code))
  })()

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Exceptions</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Individual failed conditions from a test execution — not yet a finding. An auditor investigates and decides
        whether to escalate.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      {highRiskOnly && (
        <div className="mt-3 flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-1.5 text-xs text-red-700">
          Showing only critical/high severity exceptions.
          <button onClick={clearHighRiskOnly} className="font-medium underline">
            Show all severities
          </button>
        </div>
      )}

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
              <th className="px-4 py-2">Owner</th>
              <th className="px-4 py-2">Last detected</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <Fragment key={group.code}>
                <tr className="border-t border-line bg-bg">
                  <td colSpan={7} className="px-4 py-1.5 text-xs font-semibold uppercase tracking-wide text-ink-soft">
                    {group.label} <span className="font-normal text-ink-faint">({group.items.length})</span>
                  </td>
                </tr>
                {group.items.map((e) => (
                  <ExceptionRow
                    key={e.exception_id}
                    exception={e}
                    canManage={canManage}
                    canAssign={canAssign}
                    orgUsers={orgUsers}
                    onChanged={() => organizationId && load(organizationId)}
                  />
                ))}
              </Fragment>
            ))}
            {loading && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-ink-soft">
                  Loading exceptions…
                </td>
              </tr>
            )}
            {!loading && loadError && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-red-600">
                  Could not load exceptions. Try refreshing the page.
                </td>
              </tr>
            )}
            {!loading && !loadError && visible.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-ink-soft">
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
