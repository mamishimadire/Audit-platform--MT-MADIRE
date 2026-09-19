import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { ConfirmDialog } from './ConfirmDialog'
import type { MappingReadinessOut, MappingSuggestion, TestDataMappingOut } from '../types/api'

const THRESHOLD_PRESETS = [90, 80]

export function ConfidenceBar({ value }: { value: number }) {
  const color = value >= 90 ? 'bg-accent' : value >= 60 ? 'bg-orange-400' : 'bg-red-400'
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-bg">
        <div className={`h-full ${color}`} style={{ width: `${Math.min(100, value)}%` }} />
      </div>
      <span className="text-xs text-ink-soft tabular-nums">{value.toFixed(0)}%</span>
    </div>
  )
}

const STATUS_STYLES: Record<string, string> = {
  auto: 'bg-accent-soft text-accent-ink',
  approved: 'bg-accent-soft text-accent-ink',
  needs_review: 'bg-orange-50 text-orange-700',
  manually_mapped: 'bg-bg text-ink-soft',
}

/**
 * Column-level mapping for one already-bound table — source field -> canonical
 * field, with a confidence score and an accept action, in the same shape as
 * the platform's original field-mapping mockup. Lives right under a bound
 * required table on the Controls page: binding the table and mapping its
 * columns are one continuous step, not two separate screens.
 */
export function ColumnMappingGrid({
  organizationId,
  auditTestId,
  dataSourceId,
  entityId,
}: {
  organizationId: string
  auditTestId: string
  dataSourceId: string
  entityId: string
}) {
  const [suggestions, setSuggestions] = useState<MappingSuggestion[]>([])
  const [mappings, setMappings] = useState<TestDataMappingOut[]>([])
  const [mappingId, setMappingId] = useState<string | null>(null)
  const [unmappingId, setUnmappingId] = useState<string | null>(null)
  const [threshold, setThreshold] = useState(90)
  const [autoMapping, setAutoMapping] = useState(false)
  const [approvingId, setApprovingId] = useState<string | null>(null)
  const [approveError, setApproveError] = useState<string | null>(null)
  const [rejectingId, setRejectingId] = useState<string | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  const [bulkApproving, setBulkApproving] = useState(false)
  const [bulkRejecting, setBulkRejecting] = useState(false)
  const [bulkRejectReason, setBulkRejectReason] = useState('')
  // Which canonical fields ("object.field") this control's own rule
  // template actually reads, for whichever object is bound to THIS table —
  // null means no template exists for this control at all (137 of 157
  // controls today), so there's no contract to narrow the grid down to and
  // every discovered column stays visible, same as before this existed.
  const [requiredFields, setRequiredFields] = useState<Set<string> | null>(null)
  const [showOptional, setShowOptional] = useState(false)
  // Which of those required fields the rule JOINS on — they identify rows, so they get a key marker.
  const [joinKeys, setJoinKeys] = useState<Set<string>>(new Set())

  const load = () => {
    apiClient
      .get<MappingSuggestion[]>(`/data-sources/entities/${entityId}/mapping-suggestions`, { params: { audit_test_id: auditTestId } })
      .then((res) => setSuggestions(res.data))
    apiClient
      .get<TestDataMappingOut[]>(`/organizations/${organizationId}/audit-tests/${auditTestId}/data-mappings`)
      .then((res) => setMappings(res.data.filter((m) => m.entity_id === entityId)))
    apiClient
      .get<MappingReadinessOut>(`/organizations/${organizationId}/audit-tests/${auditTestId}/template-requirements`)
      .then((res) => {
        const boundObject = res.data.objects.find((o) => o.entity_id === entityId)
        setRequiredFields(
          boundObject ? new Set(boundObject.required_fields.map((f) => `${boundObject.canonical_object}.${f.canonical_field}`)) : null,
        )
        setJoinKeys(
          new Set(
            res.data.objects.flatMap((o) => o.required_fields.filter((f) => f.is_join_key).map((f) => `${o.canonical_object}.${f.canonical_field}`)),
          ),
        )
      })
  }

  useEffect(load, [entityId, auditTestId])

  const mappingFor = (fieldId: string) => mappings.find((m) => m.field_id === fieldId)

  const accept = async (s: MappingSuggestion) => {
    setMappingId(s.field_id)
    try {
      await apiClient.post(`/organizations/${organizationId}/audit-tests/${auditTestId}/data-mappings`, {
        data_source_id: dataSourceId,
        entity_id: entityId,
        field_id: s.field_id,
        canonical_field: s.suggested_canonical_field,
        confidence_score: s.confidence_score,
      })
      load()
    } finally {
      setMappingId(null)
    }
  }

  const acceptAllAboveThreshold = async () => {
    setAutoMapping(true)
    try {
      for (const s of suggestions) {
        if (s.confidence_score >= threshold && !mappingFor(s.field_id)) {
          await apiClient.post(`/organizations/${organizationId}/audit-tests/${auditTestId}/data-mappings`, {
            data_source_id: dataSourceId,
            entity_id: entityId,
            field_id: s.field_id,
            canonical_field: s.suggested_canonical_field,
            confidence_score: s.confidence_score,
          })
        }
      }
      load()
    } finally {
      setAutoMapping(false)
    }
  }

  const confirmUnmap = async () => {
    if (!unmappingId) return
    const id = unmappingId
    setUnmappingId(null)
    await apiClient.delete(`/data-mappings/${id}`)
    load()
  }

  const approve = async (mappingId: string) => {
    setApprovingId(mappingId)
    setApproveError(null)
    try {
      await apiClient.post(`/data-mappings/${mappingId}/approve`)
      load()
    } catch (err: any) {
      setApproveError(err?.response?.data?.detail ?? 'Could not approve this mapping.')
    } finally {
      setApprovingId(null)
    }
  }

  const confirmReject = async () => {
    if (!rejectingId || !rejectReason.trim()) return
    await apiClient.post(`/data-mappings/${rejectingId}/reject`, { reason: rejectReason })
    setRejectingId(null)
    setRejectReason('')
    load()
  }

  // Not just "the ones already visible" — a mapping only shows up once
  // it's been accepted from a suggestion, so this is every mapping row on
  // this table not yet approved, same set the per-row Approve button acts on.
  const pendingMappings = mappings.filter((m) => m.mapping_status !== 'approved')

  const approveAll = async () => {
    setBulkApproving(true)
    setApproveError(null)
    try {
      for (const m of pendingMappings) {
        await apiClient.post(`/data-mappings/${m.mapping_id}/approve`)
      }
      load()
    } catch (err: any) {
      setApproveError(err?.response?.data?.detail ?? 'Could not approve every mapping — some may need a different approver.')
    } finally {
      setBulkApproving(false)
    }
  }

  const confirmRejectAll = async () => {
    if (!bulkRejectReason.trim()) return
    setBulkApproving(true)
    try {
      for (const m of pendingMappings) {
        await apiClient.post(`/data-mappings/${m.mapping_id}/reject`, { reason: bulkRejectReason })
      }
      setBulkRejecting(false)
      setBulkRejectReason('')
      load()
    } finally {
      setBulkApproving(false)
    }
  }

  if (suggestions.length === 0) {
    return <p className="mt-2 px-3 text-xs text-ink-soft">No columns discovered for this table yet.</p>
  }

  const eligibleCount = suggestions.filter((s) => s.confidence_score >= threshold && !mappingFor(s.field_id)).length

  const canonicalFieldFor = (s: MappingSuggestion) => mappingFor(s.field_id)?.canonical_field ?? s.suggested_canonical_field
  const isRequired = (s: MappingSuggestion) => requiredFields !== null && requiredFields.has(canonicalFieldFor(s))
  const requiredSuggestions = requiredFields !== null ? suggestions.filter(isRequired) : suggestions
  const optionalSuggestions = requiredFields !== null ? suggestions.filter((s) => !isRequired(s)) : []

  const renderRow = (s: MappingSuggestion) => {
    const existing = mappingFor(s.field_id)
    // An empty suggestion means nothing in the canonical model scored
    // high enough to trust — this column likely has no real canonical
    // counterpart at all (no control needs one), not that the "best"
    // match just happens to look unconvincing. Showing a specific-looking
    // field name and percentage anyway would invite trusting a number
    // that was never measuring a real correspondence.
    const noConfidentMatch = !existing && !s.suggested_canonical_field
    return (
      <tr key={s.field_id} className="border-t border-line">
        <td className="px-3 py-1.5 font-mono text-xs">
          {s.field_name}
          {!existing && s.value_fit_reason && (
            <div className="mt-0.5 font-sans text-[11px] text-amber-600" title="Checked against the column's declared type and a small sample of its values">
              ⚠ {s.value_fit_reason}
            </div>
          )}
          {!existing && s.relationship_reason && (
            <div className="mt-0.5 font-sans text-[11px] text-amber-600" title="Checked against how this table relates to the other tables this control joins">
              ⚠ {s.relationship_reason}
            </div>
          )}
        </td>
        <td className="px-3 py-1.5 font-mono text-xs text-accent-ink">
          {noConfidentMatch ? (
            <span className="italic text-ink-soft">no confident match</span>
          ) : (
            <>
              {joinKeys.has(canonicalFieldFor(s)) && (
                <span className="mr-1" title="The rule joins on this field: it identifies rows">
                  🔑
                </span>
              )}
              {existing?.canonical_field ?? s.suggested_canonical_field}
            </>
          )}
        </td>
        <td className="px-3 py-1.5">{!noConfidentMatch && <ConfidenceBar value={s.confidence_score} />}</td>
        <td className="px-3 py-1.5">
          {existing ? (
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[existing.mapping_status] ?? ''}`}>
              {existing.mapping_status === 'approved' ? '✓ Approved' : existing.mapping_status}
            </span>
          ) : noConfidentMatch ? (
            <span className="rounded-full bg-bg px-2 py-0.5 text-xs font-medium text-ink-soft">Map manually</span>
          ) : (
            <span className="rounded-full bg-orange-50 px-2 py-0.5 text-xs font-medium text-orange-700">Needs review</span>
          )}
        </td>
        <td className="px-3 py-1.5 text-right">
          <div className="flex items-center justify-end gap-2">
            {existing && existing.mapping_status !== 'approved' && (
              <>
                <button
                  onClick={() => approve(existing.mapping_id)}
                  disabled={approvingId === existing.mapping_id}
                  className="text-xs font-medium text-accent-ink hover:underline disabled:opacity-60"
                >
                  {approvingId === existing.mapping_id ? 'Approving…' : 'Approve'}
                </button>
                <button onClick={() => setRejectingId(existing.mapping_id)} className="text-xs font-medium text-red-600 hover:underline">
                  Reject
                </button>
              </>
            )}
            {existing ? (
              <button onClick={() => setUnmappingId(existing.mapping_id)} className="text-xs font-medium text-red-600 hover:underline">
                Unmap
              </button>
            ) : noConfidentMatch ? (
              <span className="text-xs text-ink-soft">—</span>
            ) : (
              <button
                onClick={() => accept(s)}
                disabled={mappingId === s.field_id}
                className="text-xs font-medium text-accent-ink hover:underline disabled:opacity-60"
              >
                {mappingId === s.field_id ? 'Mapping…' : 'Accept'}
              </button>
            )}
          </div>
        </td>
      </tr>
    )
  }

  return (
    <div className="mt-2">
      <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs text-ink-soft">
        <span>Auto-map columns scoring at least</span>
        <select
          value={THRESHOLD_PRESETS.includes(threshold) ? threshold : 'custom'}
          onChange={(e) => e.target.value !== 'custom' && setThreshold(Number(e.target.value))}
          className="rounded-md border border-line px-1.5 py-0.5 text-xs"
        >
          {THRESHOLD_PRESETS.map((p) => (
            <option key={p} value={p}>
              {p}%
            </option>
          ))}
          <option value="custom">Custom…</option>
        </select>
        <input
          type="number"
          min={0}
          max={100}
          value={threshold}
          onChange={(e) => setThreshold(Math.min(100, Math.max(0, Number(e.target.value))))}
          className="w-14 rounded-md border border-line px-1.5 py-0.5 text-xs"
        />
        <span>%</span>
        <button
          onClick={acceptAllAboveThreshold}
          disabled={autoMapping || eligibleCount === 0}
          className="rounded-md bg-accent px-2.5 py-1 text-xs font-medium text-white disabled:opacity-60"
        >
          {autoMapping ? 'Mapping…' : `Accept all (${eligibleCount})`}
        </button>
        {pendingMappings.length > 0 && (
          <span className="ml-auto flex items-center gap-2">
            <button
              onClick={approveAll}
              disabled={bulkApproving}
              className="rounded-md border border-transparent bg-accent px-2.5 py-1 text-xs font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-60"
            >
              {bulkApproving ? 'Working…' : `Approve all (${pendingMappings.length})`}
            </button>
            <button
              onClick={() => setBulkRejecting(true)}
              disabled={bulkApproving}
              className="rounded-md border border-line bg-white px-2.5 py-1 text-xs font-medium text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              Reject all ({pendingMappings.length})
            </button>
          </span>
        )}
      </div>
      {requiredFields !== null && (
        <div className="mb-1.5 text-[11px] text-ink-soft">
          {requiredFields.size > 0
            ? "Only the columns this control's test actually reads are shown by default — everything else this table has is still available below."
            : "This control has a rule template, but it doesn't read any columns from this particular table."}
        </div>
      )}
      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-3 py-1.5">Source field</th>
              <th className="px-3 py-1.5">Canonical field</th>
              <th className="px-3 py-1.5">Confidence</th>
              <th className="px-3 py-1.5">Status</th>
              <th className="px-3 py-1.5"></th>
            </tr>
          </thead>
          <tbody>
            {requiredSuggestions.map(renderRow)}
            {requiredSuggestions.length === 0 && requiredFields !== null && (
              <tr>
                <td colSpan={5} className="px-3 py-2 text-center text-xs text-ink-soft">
                  No required columns mapped yet — see "other discovered columns" below.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        {optionalSuggestions.length > 0 && (
          <>
            <button
              onClick={() => setShowOptional((v) => !v)}
              className="flex w-full items-center justify-between border-t border-line bg-bg px-3 py-1.5 text-xs font-medium text-ink-soft hover:text-ink"
            >
              <span>{optionalSuggestions.length} other discovered column{optionalSuggestions.length === 1 ? '' : 's'} (not required for this control's test)</span>
              <span>{showOptional ? 'Hide ▴' : 'Show ▾'}</span>
            </button>
            {showOptional && (
              <table className="w-full text-sm">
                <tbody>{optionalSuggestions.map(renderRow)}</tbody>
              </table>
            )}
          </>
        )}
      </div>
      {approveError && <p className="mt-1 text-xs text-red-600">{approveError}</p>}
      <ConfirmDialog
        open={unmappingId !== null}
        title="Unmap field"
        message="Remove this mapping? The test can't run against this canonical field until it's mapped again."
        confirmLabel="Unmap"
        danger
        onConfirm={confirmUnmap}
        onCancel={() => setUnmappingId(null)}
      />
      {bulkRejecting && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" onClick={() => setBulkRejecting(false)}>
          <div className="w-full max-w-sm rounded-lg border border-line bg-surface p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="text-sm font-semibold text-ink">Reject all pending mappings ({pendingMappings.length})</div>
            <p className="mt-2 text-sm text-ink-soft">A reason is required — it applies to every pending mapping on this table and goes back to the mapper to fix and resubmit.</p>
            <textarea
              autoFocus
              placeholder="e.g. Wrong table entirely — this should map against the archive schema, not the live one."
              value={bulkRejectReason}
              onChange={(e) => setBulkRejectReason(e.target.value)}
              className="mt-2 w-full rounded-md border border-line px-2 py-1 text-sm"
              rows={3}
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                onClick={() => { setBulkRejecting(false); setBulkRejectReason('') }}
                className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-ink hover:bg-bg"
              >
                Cancel
              </button>
              <button
                onClick={confirmRejectAll}
                disabled={!bulkRejectReason.trim() || bulkApproving}
                className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
              >
                {bulkApproving ? 'Working…' : 'Reject all'}
              </button>
            </div>
          </div>
        </div>
      )}
      {rejectingId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" onClick={() => setRejectingId(null)}>
          <div className="w-full max-w-sm rounded-lg border border-line bg-surface p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="text-sm font-semibold text-ink">Reject mapping</div>
            <p className="mt-2 text-sm text-ink-soft">A reason is required — it goes back to the mapper to fix and resubmit.</p>
            <textarea
              autoFocus
              placeholder="e.g. Wrong column — TERM_DT is the last review date, not the termination date."
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              className="mt-2 w-full rounded-md border border-line px-2 py-1 text-sm"
              rows={3}
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                onClick={() => { setRejectingId(null); setRejectReason('') }}
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
