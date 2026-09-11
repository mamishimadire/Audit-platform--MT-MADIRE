import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { ColumnMappingGrid } from './ColumnMapping'
import { ConfirmDialog } from './ConfirmDialog'
import type { DataEntityOut, DataSourceOut, TableBindingProgressOut } from '../types/api'

export interface BindingSuggestion {
  data_source_id: string
  entity_id: string
  source_name: string | null
  entity_name: string | null
  // 'reused': this exact table is already bound elsewhere in the org for a
  // different control — the strongest possible signal, since a human
  // already confirmed it. 'name_match': nobody has bound this canonical
  // table anywhere yet — the platform is guessing from discovered-table
  // names alone (see score_table_name_match), so it always carries a
  // confidence score the picker can show plainly as a guess, not a fact.
  source: 'reused' | 'name_match'
  confidence_score?: number
}

/**
 * Same canonical table (e.g. system_users) is required by dozens of
 * controls. Once it's bound anywhere in this org, every other control
 * that needs it should default to that same binding instead of asking
 * the auditor to re-search for it — that reuse always outranks a fresh
 * name-match guess. Only for a table nobody has bound anywhere yet does
 * this fall back to the backend's name-similarity suggestion (progress.
 * suggestions, computed once per control against every discovered table
 * in the org — see control_binding_service._suggest_bindings).
 */
export function suggestionsFromProgress(bindingProgress: Record<string, TableBindingProgressOut>): Record<string, BindingSuggestion> {
  const reused: Record<string, BindingSuggestion & { bound_at: string }> = {}
  for (const progress of Object.values(bindingProgress)) {
    for (const b of progress.bindings) {
      if (b.status !== 'bound' || !b.data_source_id || !b.entity_id) continue
      const existing = reused[b.canonical_table_name]
      if (!existing || b.bound_at > existing.bound_at) {
        reused[b.canonical_table_name] = {
          data_source_id: b.data_source_id,
          entity_id: b.entity_id,
          source_name: b.source_name,
          entity_name: b.entity_name,
          source: 'reused',
          bound_at: b.bound_at,
        }
      }
    }
  }

  const suggestions: Record<string, BindingSuggestion> = { ...reused }
  for (const progress of Object.values(bindingProgress)) {
    for (const [table, candidates] of Object.entries(progress.suggestions)) {
      if (suggestions[table] || candidates.length === 0) continue // reuse already wins, or nothing to suggest
      const top = candidates[0]
      suggestions[table] = {
        data_source_id: top.data_source_id,
        entity_id: top.entity_id,
        source_name: top.source_name,
        entity_name: top.entity_name,
        source: 'name_match',
        confidence_score: top.confidence_score,
      }
    }
  }
  return suggestions
}

export function TableBindingPicker({
  organizationId,
  controlId,
  canonicalTableName,
  sources,
  suggestion,
  onDone,
}: {
  organizationId: string
  controlId: string
  canonicalTableName: string
  sources: DataSourceOut[]
  suggestion?: BindingSuggestion
  onDone: () => void
}) {
  const [mode, setMode] = useState<'bind' | 'not_applicable'>('bind')
  const [sourceId, setSourceId] = useState(suggestion?.data_source_id ?? '')
  const [entities, setEntities] = useState<DataEntityOut[]>([])
  const [entityId, setEntityId] = useState(suggestion?.entity_id ?? '')
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!sourceId) return
    apiClient.get<DataEntityOut[]>(`/data-sources/${sourceId}/entities`).then((res) => setEntities(res.data))
    // A suggested entity belongs to the suggested source — don't clear it just
    // because the source select re-fired on mount with the same value.
    setEntityId((current) => (sourceId === suggestion?.data_source_id ? current : ''))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceId])

  const bind = async () => {
    setSaving(true)
    setError(null)
    try {
      await apiClient.post(`/organizations/${organizationId}/controls/${controlId}/table-bindings`, {
        canonical_table_name: canonicalTableName,
        data_source_id: sourceId,
        entity_id: entityId,
      })
      onDone()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not bind this table.')
    } finally {
      setSaving(false)
    }
  }

  const markNotApplicable = async () => {
    setSaving(true)
    setError(null)
    try {
      await apiClient.post(`/organizations/${organizationId}/controls/${controlId}/table-bindings/not-applicable`, {
        canonical_table_name: canonicalTableName,
        reason,
      })
      onDone()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not save.')
    } finally {
      setSaving(false)
    }
  }

  if (mode === 'not_applicable') {
    return (
      <div className="mt-1 flex flex-1 flex-wrap items-center gap-2">
        <textarea
          placeholder="Why doesn't this apply to this organization? (required, logged for audit evidence)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          className="w-full flex-1 rounded-md border border-line px-2 py-1 text-xs"
          rows={1}
        />
        <button onClick={markNotApplicable} disabled={saving || !reason.trim()} className="whitespace-nowrap rounded-md bg-accent px-3 py-1 text-xs font-medium text-white disabled:opacity-60">
          {saving ? 'Saving…' : 'Confirm not applicable'}
        </button>
        <button onClick={() => setMode('bind')} className="whitespace-nowrap text-xs text-ink-soft hover:underline">
          Cancel
        </button>
        {error && <p className="w-full text-xs text-red-600">{error}</p>}
      </div>
    )
  }

  return (
    <div className="flex flex-1 flex-wrap items-center gap-2">
      <select value={sourceId} onChange={(e) => setSourceId(e.target.value)} className="rounded-md border border-line px-2 py-1 text-xs">
        <option value="">Select data source…</option>
        {sources.map((s) => (
          <option key={s.data_source_id} value={s.data_source_id}>
            {s.source_name}
          </option>
        ))}
      </select>
      <select value={entityId} onChange={(e) => setEntityId(e.target.value)} disabled={!sourceId} className="rounded-md border border-line px-2 py-1 text-xs">
        <option value="">Select table…</option>
        {entities.map((e) => (
          <option key={e.entity_id} value={e.entity_id}>
            {e.entity_name}
          </option>
        ))}
      </select>
      <button onClick={bind} disabled={saving || !sourceId || !entityId} className="whitespace-nowrap rounded-md bg-accent px-3 py-1 text-xs font-medium text-white disabled:opacity-60">
        {saving ? 'Binding…' : suggestion ? 'Confirm' : 'Bind'}
      </button>
      <button onClick={() => setMode('not_applicable')} className="whitespace-nowrap text-xs text-ink-soft hover:underline">
        Not applicable
      </button>
      {suggestion && sourceId === suggestion.data_source_id && entityId === suggestion.entity_id && (
        <p className="w-full text-xs text-accent-ink">
          {suggestion.source === 'reused'
            ? 'Suggested from another control that already uses this table — confirm, or change it above.'
            : `Suggested by name match (${Math.round(suggestion.confidence_score ?? 0)}% confidence) — a guess, not a confirmed binding. Review before confirming, or change it above.`}
        </p>
      )}
      {error && <p className="w-full text-xs text-red-600">{error}</p>}
    </div>
  )
}

/**
 * The required-tables binding checklist. Shared by the Controls page
 * (Activated tab row, library browse row) and the Audit Test mapping
 * screen — every place a control's required tables need to be named and
 * bindable must use the exact same behavior, not separate copies that
 * can drift out of sync with each other.
 */
export function RequiredTablesChecklist({
  controlId,
  auditTestId,
  requiredTables,
  organizationId,
  sources,
  progress,
  suggestions,
  onRefreshProgress,
  canManage,
}: {
  controlId: string
  /** When known, a bound table's columns are mapped right here instead of stopping at "table bound." */
  auditTestId?: string
  requiredTables: string[]
  organizationId: string
  sources: DataSourceOut[]
  progress: TableBindingProgressOut | undefined
  suggestions: Record<string, BindingSuggestion>
  onRefreshProgress: (controlId: string) => void
  canManage: boolean
}) {
  const [editingTable, setEditingTable] = useState<string | null>(null)
  const [unbindingTable, setUnbindingTable] = useState<string | null>(null)
  const bindingFor = (table: string) => progress?.bindings.find((b) => b.canonical_table_name === table)

  const confirmUnbind = async () => {
    if (!unbindingTable) return
    const table = unbindingTable
    setUnbindingTable(null)
    await apiClient.delete(`/organizations/${organizationId}/controls/${controlId}/table-bindings/${encodeURIComponent(table)}`)
    onRefreshProgress(controlId)
  }

  if (!progress) return <p className="mt-2 text-xs text-ink-soft">Loading…</p>

  return (
    <div className="mt-2 divide-y divide-line rounded-md border border-line bg-surface">
      {requiredTables.map((table) => {
        const binding = bindingFor(table)
        const suggestion = suggestions[table]
        const showPicker = binding ? editingTable === table : true
        const ready = binding !== undefined
        const showColumns = canManage && binding?.status === 'bound' && binding.entity_id && binding.data_source_id && auditTestId && editingTable !== table
        return (
          <div key={table} className="px-3 py-2">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className={`shrink-0 ${ready ? 'text-accent-ink' : 'text-red-600'}`}>{ready ? '●' : '○'}</span>
              <span className="w-40 shrink-0 font-mono text-xs text-ink">{table}</span>
              <span className="shrink-0 text-ink-soft">→</span>
              {showPicker && canManage ? (
                <TableBindingPicker
                  key={table}
                  organizationId={organizationId}
                  controlId={controlId}
                  canonicalTableName={table}
                  sources={sources}
                  suggestion={suggestion}
                  onDone={() => {
                    setEditingTable(null)
                    onRefreshProgress(controlId)
                  }}
                />
              ) : (
                <div className="flex flex-1 flex-wrap items-center gap-2">
                  {binding ? (
                    binding.status === 'bound' ? (
                      <span className="text-xs text-accent-ink">✓ {binding.source_name} · {binding.entity_name}</span>
                    ) : (
                      <span className="text-xs text-ink-soft">Not applicable — {binding.not_applicable_reason}</span>
                    )
                  ) : (
                    <span className="text-xs text-red-600">
                      Not mapped
                      {suggestion &&
                        (suggestion.source === 'reused'
                          ? ' — suggestion available (reused from another control)'
                          : ` — recommended: ${suggestion.entity_name} (${Math.round(suggestion.confidence_score ?? 0)}% name match)`)}
                    </span>
                  )}
                  {canManage && binding && (
                    <button onClick={() => setEditingTable(table)} className="text-xs font-medium text-accent-ink hover:underline">
                      Change
                    </button>
                  )}
                  {canManage && binding && (
                    <button onClick={() => setUnbindingTable(table)} className="text-xs font-medium text-red-600 hover:underline">
                      Unbind
                    </button>
                  )}
                </div>
              )}
            </div>
            {showColumns && (
              <ColumnMappingGrid
                organizationId={organizationId}
                auditTestId={auditTestId}
                dataSourceId={binding!.data_source_id!}
                entityId={binding!.entity_id!}
              />
            )}
          </div>
        )
      })}
      <ConfirmDialog
        open={unbindingTable !== null}
        title="Unbind table"
        message={`Clear the binding for '${unbindingTable}'? Any column mappings made against it will no longer resolve, and it goes back to "Not mapped."`}
        confirmLabel="Unbind"
        danger
        onConfirm={confirmUnbind}
        onCancel={() => setUnbindingTable(null)}
      />
    </div>
  )
}
