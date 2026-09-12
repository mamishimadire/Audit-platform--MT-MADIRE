import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { AUDIT_FRAMEWORK_ROLES } from '../auth/permissions'
import { ConfirmDialog } from './ConfirmDialog'
import { ConfidenceBar } from './ColumnMapping'
import { RequiredTablesChecklist, suggestionsFromProgress } from './TableBinding'
import { RelationshipValidationPanel } from './RelationshipValidation'
import type { DataEntityOut, DataSourceOut, MappingReadinessOut, MappingSuggestion, TableBindingProgressOut, TestDataMappingOut } from '../types/api'

const STATUS_STYLES: Record<string, string> = {
  auto: 'bg-accent-soft text-accent-ink',
  approved: 'bg-accent-soft text-accent-ink',
  needs_review: 'bg-orange-50 text-orange-700',
  manually_mapped: 'bg-bg text-ink-soft',
}

interface Props {
  organizationId: string
  auditTestId: string
  controlId: string | null
  requiredTables: string[]
}

export function DataMappingPanel({ organizationId, auditTestId, controlId, requiredTables }: Props) {
  const { hasRole } = useAuth()
  const canManage = hasRole(...AUDIT_FRAMEWORK_ROLES)
  const [readiness, setReadiness] = useState<MappingReadinessOut | null>(null)
  const [sources, setSources] = useState<DataSourceOut[]>([])
  const [sourceId, setSourceId] = useState('')
  const [entities, setEntities] = useState<DataEntityOut[]>([])
  const [entityId, setEntityId] = useState('')
  const [suggestions, setSuggestions] = useState<MappingSuggestion[]>([])
  const [mappings, setMappings] = useState<TestDataMappingOut[]>([])
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  // The object currently being worked on (from clicking "Map this table" in
  // the readiness checklist) — narrows suggestions to what this test's rule
  // actually reads, instead of every column a table happens to have.
  const [targetObject, setTargetObject] = useState<string | null>(null)
  const [unmappingId, setUnmappingId] = useState<string | null>(null)
  const [tableProgress, setTableProgress] = useState<TableBindingProgressOut | null>(null)

  const loadReadiness = () =>
    apiClient
      .get<MappingReadinessOut>(`/organizations/${organizationId}/audit-tests/${auditTestId}/mapping-readiness`)
      .then((res) => setReadiness(res.data))

  const loadMappings = () =>
    apiClient
      .get<TestDataMappingOut[]>(`/organizations/${organizationId}/audit-tests/${auditTestId}/data-mappings`)
      .then((res) => setMappings(res.data))

  const loadTableProgress = () => {
    if (!controlId) return
    apiClient
      .get<TableBindingProgressOut>(`/organizations/${organizationId}/controls/${controlId}/table-bindings`)
      .then((res) => setTableProgress(res.data))
  }

  const reloadAll = () => {
    loadMappings()
    loadReadiness()
  }

  useEffect(() => {
    apiClient.get<DataSourceOut[]>(`/organizations/${organizationId}/data-sources`).then((res) => setSources(res.data))
    reloadAll()
    loadTableProgress()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!sourceId) return
    apiClient.get<DataEntityOut[]>(`/data-sources/${sourceId}/entities`).then((res) => setEntities(res.data))
    setEntityId('')
    setSuggestions([])
  }, [sourceId])

  useEffect(() => {
    if (!entityId) return
    apiClient.get<MappingSuggestion[]>(`/data-sources/entities/${entityId}/mapping-suggestions`).then((res) => setSuggestions(res.data))
  }, [entityId])

  const alreadyMapped = (fieldId: string) => mappings.some((m) => m.field_id === fieldId)

  const requiredFieldNamesForTarget = targetObject
    ? new Set(readiness?.objects.find((o) => o.canonical_object === targetObject)?.required_fields.map((f) => f.canonical_field) ?? [])
    : null

  const startMappingObject = (canonicalObject: string) => {
    setTargetObject(canonicalObject)
    const obj = readiness?.objects.find((o) => o.canonical_object === canonicalObject)
    if (obj?.data_source_id) setSourceId(obj.data_source_id)
    if (obj?.entity_id) setEntityId(obj.entity_id)
  }

  const createMapping = async (s: MappingSuggestion, canonicalField: string) => {
    await apiClient.post(`/organizations/${organizationId}/audit-tests/${auditTestId}/data-mappings`, {
      data_source_id: sourceId,
      entity_id: entityId,
      field_id: s.field_id,
      canonical_field: canonicalField,
      confidence_score: s.confidence_score,
    })
    reloadAll()
  }

  const createAllAuto = async () => {
    for (const s of suggestions) {
      const canonicalField = targetObject ? `${targetObject}.${s.suggested_canonical_field.split('.').pop()}` : s.suggested_canonical_field
      if (s.confidence_score >= 90 && !alreadyMapped(s.field_id)) await createMapping(s, canonicalField)
    }
  }

  const saveEdit = async (mappingId: string) => {
    await apiClient.patch(`/data-mappings/${mappingId}`, { canonical_field: editValue })
    setEditingId(null)
    reloadAll()
  }

  const [approveError, setApproveError] = useState<string | null>(null)

  const approve = async (mappingId: string) => {
    setApproveError(null)
    try {
      await apiClient.post(`/data-mappings/${mappingId}/approve`)
      reloadAll()
    } catch (err: any) {
      setApproveError(err?.response?.status === 403 ? err.response.data?.detail ?? 'A different authorized user must approve this mapping.' : 'Could not approve this mapping.')
    }
  }

  const doUnmap = async () => {
    if (!unmappingId) return
    const id = unmappingId
    setUnmappingId(null)
    await apiClient.delete(`/data-mappings/${id}`)
    reloadAll()
  }

  // A control's required tables are the whole story once this checklist
  // applies — each row already carries its own source/table pickers and
  // (once bound) its own column-mapping grid, so the older generic
  // "pick any table, see a flat suggestions list" flow below would just be
  // a second, disconnected way to do the same thing. Only fall back to it
  // for a manually-created test with no linked control.
  const hasControlChecklist = Boolean(controlId && requiredTables.length > 0)

  return (
    <div className="space-y-4 bg-bg p-4">
      {hasControlChecklist && (
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">
            This control's required tables{requiredTables.length > 1 ? ' — bind each one below' : ''}
          </div>
          <RequiredTablesChecklist
            controlId={controlId!}
            auditTestId={auditTestId}
            requiredTables={requiredTables}
            organizationId={organizationId}
            sources={sources}
            progress={tableProgress ?? undefined}
            suggestions={tableProgress ? suggestionsFromProgress({ [controlId!]: tableProgress }) : {}}
            onRefreshProgress={loadTableProgress}
            canManage={canManage}
          />
          <RelationshipValidationPanel organizationId={organizationId} auditTestId={auditTestId} />
        </div>
      )}
      {!hasControlChecklist && readiness && !readiness.has_rule && (
        <p className="rounded-md border border-line bg-surface px-3 py-2 text-xs text-ink-soft">
          No test rule yet — create one below to see exactly which tables and fields this test will need.
        </p>
      )}
      {!hasControlChecklist && readiness && readiness.has_rule && readiness.objects.length > 0 && (
        <div>
          <div className="flex items-center justify-between">
            <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">
              Tables required for this test
            </div>
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${readiness.ready ? 'bg-accent-soft text-accent-ink' : 'bg-orange-50 text-orange-700'}`}>
              {readiness.ready ? '✓ Fully mapped — ready to test' : 'Not ready to test yet'}
            </span>
          </div>
          <div className="mt-2 space-y-2">
            {readiness.objects.map((obj) => (
              <div key={obj.canonical_object} className="rounded-lg border border-line bg-surface p-3">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-medium text-ink">
                    <span className="font-mono">{obj.canonical_object}</span>
                    {obj.entity_name ? (
                      <span className="ml-2 text-xs text-ink-soft">
                        → {obj.source_name} · {obj.entity_name}
                      </span>
                    ) : (
                      <span className="ml-2 text-xs text-ink-soft">— no table selected yet</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${obj.fully_mapped ? 'bg-accent-soft text-accent-ink' : 'bg-orange-50 text-orange-700'}`}>
                      {obj.fully_mapped ? '✓ Mapped' : 'Not mapped'}
                    </span>
                    {canManage && (
                      <button
                        onClick={() => startMappingObject(obj.canonical_object)}
                        className="text-xs font-medium text-accent-ink hover:underline"
                      >
                        {obj.entity_name ? 'Change table' : 'Map this table'}
                      </button>
                    )}
                  </div>
                </div>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
                  {obj.required_fields.map((f) => (
                    <span key={f.canonical_field} className={`flex items-center gap-1 text-xs ${f.mapped ? 'text-accent-ink' : 'text-red-600'}`}>
                      {f.mapped ? '✓' : '✗'} {f.canonical_field}
                      {f.mapped && f.field_name && <span className="text-ink-soft"> ({f.field_name})</span>}
                      {canManage && f.mapped && f.mapping_id && (
                        <button onClick={() => setUnmappingId(f.mapping_id)} className="font-medium text-red-600 hover:underline">
                          Unmap
                        </button>
                      )}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <p className="mt-1 text-xs text-ink-soft">
            Only these fields matter for this test's rule — a table can have many other columns that are never used.
          </p>
        </div>
      )}

      {!hasControlChecklist && canManage && (
        <div>
          {targetObject && (
            <div className="mb-2 flex items-center gap-2 rounded-md bg-accent-soft px-2 py-1 text-xs text-accent-ink">
              Mapping table for <span className="font-mono font-medium">{targetObject}</span>
              <button onClick={() => setTargetObject(null)} className="ml-auto font-medium hover:underline">
                Clear
              </button>
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <select value={sourceId} onChange={(e) => setSourceId(e.target.value)} className="rounded-md border border-line px-2 py-1 text-sm">
              <option value="">Select data source…</option>
              {sources.map((s) => (
                <option key={s.data_source_id} value={s.data_source_id}>
                  {s.source_name}
                </option>
              ))}
            </select>
            <select
              value={entityId}
              onChange={(e) => setEntityId(e.target.value)}
              disabled={!sourceId}
              className="rounded-md border border-line px-2 py-1 text-sm"
            >
              <option value="">Select table…</option>
              {entities.map((e) => (
                <option key={e.entity_id} value={e.entity_id}>
                  {e.entity_name}
                </option>
              ))}
            </select>
            {suggestions.length > 0 && (
              <button onClick={createAllAuto} className="rounded-md bg-accent px-3 py-1 text-xs font-medium text-white">
                Accept all ≥90% suggestions
              </button>
            )}
          </div>
        </div>
      )}

      {!hasControlChecklist && canManage && suggestions.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-line bg-surface">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
                <th className="px-3 py-2">Source field</th>
                <th className="px-3 py-2">Suggested canonical field</th>
                <th className="px-3 py-2">Confidence</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {suggestions.map((s) => {
                const suggestedName = s.suggested_canonical_field.split('.').pop() ?? s.suggested_canonical_field
                const canonicalField = targetObject ? `${targetObject}.${suggestedName}` : s.suggested_canonical_field
                const isRequired = requiredFieldNamesForTarget?.has(suggestedName) ?? false
                // When targeting a specific object, a field this rule doesn't
                // actually read is still shown (context is useful) but
                // de-emphasized — mapping it is harmless, just not necessary.
                return (
                  <tr key={s.field_id} className={`border-t border-line ${targetObject && !isRequired ? 'opacity-50' : ''}`}>
                    <td className="px-3 py-2 font-mono text-xs">{s.field_name}</td>
                    <td className="px-3 py-2 font-mono text-xs text-accent-ink">
                      {canonicalField}
                      {targetObject && isRequired && <span className="ml-1 rounded-full bg-accent-soft px-1.5 py-0.5 text-[10px] font-sans text-accent-ink">required</span>}
                    </td>
                    <td className="px-3 py-2">
                      <ConfidenceBar value={s.confidence_score} />
                    </td>
                    <td className="px-3 py-2">
                      {alreadyMapped(s.field_id) ? (
                        <span className="text-xs text-ink-soft">mapped</span>
                      ) : (
                        <button onClick={() => createMapping(s, canonicalField)} className="text-xs font-medium text-accent-ink hover:underline">
                          Map
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {!hasControlChecklist && (
      <div>
        <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Mappings for this test</div>
        {approveError && <p className="mt-1 text-xs text-red-600">{approveError}</p>}
        <div className="mt-2 overflow-hidden rounded-lg border border-line bg-surface">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
                <th className="px-3 py-2">Canonical field</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {mappings.map((m) => (
                <tr key={m.mapping_id} className="border-t border-line">
                  <td className="px-3 py-2 font-mono text-xs">
                    {editingId === m.mapping_id ? (
                      <input
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        className="rounded border border-line px-1 py-0.5 text-xs"
                      />
                    ) : (
                      m.canonical_field
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[m.mapping_status] ?? ''}`}>
                      {m.mapping_status}
                    </span>
                  </td>
                  <td className="px-3 py-2 space-x-2">
                    {canManage && (
                      <>
                        {editingId === m.mapping_id ? (
                          <button onClick={() => saveEdit(m.mapping_id)} className="text-xs font-medium text-accent-ink hover:underline">
                            Save
                          </button>
                        ) : (
                          <button
                            onClick={() => {
                              setEditingId(m.mapping_id)
                              setEditValue(m.canonical_field ?? '')
                            }}
                            className="text-xs font-medium text-ink-soft hover:underline"
                          >
                            Edit
                          </button>
                        )}
                        {m.mapping_status !== 'approved' && (
                          <button onClick={() => approve(m.mapping_id)} className="text-xs font-medium text-accent-ink hover:underline">
                            Approve
                          </button>
                        )}
                        <button onClick={() => setUnmappingId(m.mapping_id)} className="text-xs font-medium text-red-600 hover:underline">
                          Unmap
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
              {mappings.length === 0 && (
                <tr>
                  <td colSpan={3} className="px-3 py-4 text-center text-xs text-ink-soft">
                    No mappings yet — select a table above to see suggestions.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      )}
      <ConfirmDialog
        open={unmappingId !== null}
        title="Unmap field"
        message="Remove this mapping? The test can't run against this canonical field until it's mapped again."
        confirmLabel="Unmap"
        danger
        onConfirm={doUnmap}
        onCancel={() => setUnmappingId(null)}
      />
    </div>
  )
}
