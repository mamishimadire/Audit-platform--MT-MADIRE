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

// A small "MM" / "TN" avatar circle — dark navy for the internal audit
// team, teal for the client side — so a thread with both sides posting
// reads at a glance without having to read every name.
function userAvatar(orgUsers: UserOut[], userId: string | null): { initials: string; internal: boolean } {
  const u = orgUsers.find((x) => x.user_id === userId)
  if (!u) return { initials: '?', internal: false }
  const initials = `${u.first_name[0] ?? ''}${u.last_name[0] ?? ''}`.toUpperCase()
  const internal = u.roles.some((r) => (AUDIT_FRAMEWORK_ROLES as readonly string[]).includes(r))
  return { initials, internal }
}

function Avatar({ orgUsers, userId }: { orgUsers: UserOut[]; userId: string | null }) {
  const { initials, internal } = userAvatar(orgUsers, userId)
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

// A small colored file-type badge ("PDF", "XLS", "?" while still
// awaiting) — the same visual shorthand the product spec's mockup uses,
// so a list of requests reads at a glance instead of as plain text.
function fileTypeBadge(fileName: string | null): { label: string; classes: string } {
  const ext = fileName?.split('.').pop()?.toUpperCase() ?? ''
  if (ext === 'PDF') return { label: 'PDF', classes: 'bg-red-50 text-red-700' }
  if (ext === 'XLS' || ext === 'XLSX') return { label: 'XLS', classes: 'bg-accent-soft text-accent-ink' }
  if (ext === 'CSV') return { label: 'CSV', classes: 'bg-accent-soft text-accent-ink' }
  if (ext === 'DOC' || ext === 'DOCX') return { label: 'DOC', classes: 'bg-blue-50 text-blue-700' }
  if (ext) return { label: ext.slice(0, 3), classes: 'bg-bg text-ink-soft' }
  return { label: '?', classes: 'bg-accent-soft text-accent-ink' }
}

function FileBadge({ fileName }: { fileName: string | null }) {
  const { label, classes } = fileTypeBadge(fileName)
  return (
    <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[10px] font-bold ${classes}`}>
      {label}
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

function humanizeExceptionValue(key: string, value: unknown, data: Record<string, unknown>): string {
  if (key === 'check' && typeof value === 'string') return COMPLIANCE_CHECK_NAMES[value] ?? titleCaseFieldName(value)
  if (key === 'value' && typeof data.check === 'string') return value === false ? 'Failing' : value === true ? 'Passing' : String(value)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (value === null || value === undefined || value === '') return 'Not reported'
  if (typeof value === 'object') return JSON.stringify(value)
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

  const [trace, setTrace] = useState<ExceptionTraceOut | null>(null)
  const [showTrace, setShowTrace] = useState(false)
  const [traceLoading, setTraceLoading] = useState(false)

  const loadCollaboration = () => {
    apiClient.get<EvidenceRequestOut[]>(`/exceptions/${exception.exception_id}/evidence-requests`).then((res) => setEvidenceRequests(res.data))
    apiClient.get<ExceptionCommentOut[]>(`/exceptions/${exception.exception_id}/comments`).then((res) => setComments(res.data))
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

  const downloadFile = async (requestId: string, fileName: string) => {
    const res = await apiClient.get(`/evidence-requests/${requestId}/file`, { responseType: 'blob' })
    const url = URL.createObjectURL(res.data as Blob)
    const link = document.createElement('a')
    link.href = url
    link.download = fileName
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
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

            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div className="rounded-md border border-line bg-surface p-3">
                <div className="flex items-center justify-between">
                  <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Evidence requested from client</div>
                  {canManage && !requestingEvidence && (
                    <button onClick={() => setRequestingEvidence(true)} className="text-xs font-medium text-accent-ink hover:underline">
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
                      <FileBadge fileName={r.status === 'received' ? r.file_name : null} />
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
                        {r.status === 'awaiting' ? (
                          <div className="text-ink-soft">
                            Requested {new Date(r.requested_at).toLocaleDateString()}
                            {userLabel(orgUsers, r.requested_by) && ` by ${userLabel(orgUsers, r.requested_by)}`}
                            {r.due_date && ` · due ${r.due_date}`} · awaiting upload
                          </div>
                        ) : (
                          <div className="text-ink-soft">
                            Uploaded {userLabel(orgUsers, r.uploaded_by) ? `by ${userLabel(orgUsers, r.uploaded_by)}` : ''}
                            {r.uploaded_at && ` · ${new Date(r.uploaded_at).toLocaleString()}`}
                          </div>
                        )}
                        {r.status === 'received' ? (
                          <button onClick={() => downloadFile(r.request_id, r.file_name ?? 'evidence')} className="mt-1 font-mono text-accent-ink hover:underline">
                            {r.file_name}
                          </button>
                        ) : (
                          <label className="mt-1 inline-block cursor-pointer rounded-md border border-line px-2 py-1 text-[11px] font-medium text-ink hover:bg-bg">
                            {uploadingRequestId === r.request_id ? 'Uploading…' : 'Upload file'}
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
                      <Avatar orgUsers={orgUsers} userId={c.author_id} />
                      <div className="min-w-0 flex-1 rounded-md bg-bg p-2">
                        <div className="font-medium text-ink">
                          {userLabel(orgUsers, c.author_id) ?? 'Unknown user'}{' '}
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
