import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { ConfirmDialog } from './ConfirmDialog'
import type { MappingSuggestion, TestDataMappingOut } from '../types/api'

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

  const load = () => {
    apiClient.get<MappingSuggestion[]>(`/data-sources/entities/${entityId}/mapping-suggestions`).then((res) => setSuggestions(res.data))
    apiClient
      .get<TestDataMappingOut[]>(`/organizations/${organizationId}/audit-tests/${auditTestId}/data-mappings`)
      .then((res) => setMappings(res.data.filter((m) => m.entity_id === entityId)))
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

  if (suggestions.length === 0) {
    return <p className="mt-2 px-3 text-xs text-ink-soft">No columns discovered for this table yet.</p>
  }

  const eligibleCount = suggestions.filter((s) => s.confidence_score >= threshold && !mappingFor(s.field_id)).length

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
      </div>
      <div className="overflow-hidden rounded-lg border border-line bg-surface">
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
            {suggestions.map((s) => {
              const existing = mappingFor(s.field_id)
              return (
                <tr key={s.field_id} className="border-t border-line">
                  <td className="px-3 py-1.5 font-mono text-xs">{s.field_name}</td>
                  <td className="px-3 py-1.5 font-mono text-xs text-accent-ink">{existing?.canonical_field ?? s.suggested_canonical_field}</td>
                  <td className="px-3 py-1.5">
                    <ConfidenceBar value={s.confidence_score} />
                  </td>
                  <td className="px-3 py-1.5">
                    {existing ? (
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[existing.mapping_status] ?? ''}`}>
                        {existing.mapping_status === 'approved' ? '✓ Approved' : existing.mapping_status}
                      </span>
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
            })}
          </tbody>
        </table>
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
