import { Fragment, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { AUDIT_FRAMEWORK_ROLES } from '../auth/permissions'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import { RequiredTablesChecklist, suggestionsFromProgress, type BindingSuggestion } from '../components/TableBinding'
import type { AuditTestOut, ControlLibraryOut, ControlOut, DataSourceOut, RiskOut, TableBindingProgressOut } from '../types/api'

/**
 * Activation and deactivation each go through a request/approve pair, with
 * deactivation approval mandatory (never skippable) — the same identity
 * check the backend enforces (requester != approver). This component
 * doesn't try to guess whether the current viewer is allowed to approve
 * their own request; it just attempts the action and surfaces the
 * backend's rejection if so, same as everywhere else in this app.
 */
function ControlLifecycleActions({
  control,
  organizationId,
  onChanged,
  progress,
}: {
  control: ControlOut
  organizationId: string
  onChanged: () => void
  progress?: TableBindingProgressOut
}) {
  const [busy, setBusy] = useState(false)
  // Only block on readiness once we actually know it (progress loaded) —
  // never render the button live-and-clickable only to fail after the
  // click, but also never falsely block it during the brief window before
  // progress has loaded for a control with required tables.
  const notReady = control.required_tables.length > 0 && progress !== undefined && !progress.ready
  const missingTables = notReady
    ? progress!.required_tables.filter((t) => !progress!.bindings.some((b) => b.canonical_table_name === t))
    : []
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<'deactivate' | 'reject-activation' | 'reject-deactivation' | null>(null)
  const [reason, setReason] = useState('')

  const post = async (action: string, body?: object) => {
    setBusy(true)
    setError(null)
    try {
      await apiClient.post(`/organizations/${organizationId}/controls/${control.control_id}/${action}`, body)
      setMode(null)
      setReason('')
      onChanged()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not complete this action.')
    } finally {
      setBusy(false)
    }
  }

  const reasonBox = (label: string, onConfirm: () => void) => (
    <div className="text-right">
      <textarea
        autoFocus
        placeholder="Reason (required, logged for audit evidence)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        className="w-64 rounded-md border border-line px-2 py-1 text-xs"
        rows={2}
      />
      <div className="mt-1 flex justify-end gap-2">
        <button onClick={() => { setMode(null); setReason('') }} className="text-xs text-ink-soft hover:underline">
          Cancel
        </button>
        <button
          onClick={onConfirm}
          disabled={busy || !reason.trim()}
          className="rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white disabled:opacity-60"
        >
          {busy ? 'Saving…' : label}
        </button>
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  )

  if (mode === 'deactivate') return reasonBox('Confirm request', () => post('deactivation/request', { reason }))
  if (mode === 'reject-activation') return reasonBox('Confirm rejection', () => post('activation/reject', { reason }))
  if (mode === 'reject-deactivation') return reasonBox('Confirm rejection', () => post('deactivation/reject', { reason }))

  return (
    <div className="text-right">
      {control.status === 'pending_mapping' && (
        <>
          <button
            onClick={() => post('activation/request')}
            disabled={busy || notReady}
            title={notReady ? `Bind every required table first — still missing: ${missingTables.join(', ')}.` : undefined}
            className="text-xs font-medium text-accent-ink hover:underline disabled:cursor-not-allowed disabled:text-ink-faint disabled:no-underline"
          >
            {busy ? 'Requesting…' : 'Request activation'}
          </button>
          {notReady && (
            <p className="mt-0.5 text-[11px] text-ink-faint">
              {progress!.satisfied} of {progress!.total} required tables bound — missing {missingTables.join(', ')}.
            </p>
          )}
        </>
      )}
      {control.status === 'pending_activation' && (
        <div className="flex justify-end gap-2">
          <button onClick={() => post('activation/approve')} disabled={busy} className="text-xs font-medium text-accent-ink hover:underline disabled:opacity-60">
            {busy ? 'Approving…' : 'Approve activation'}
          </button>
          <button onClick={() => setMode('reject-activation')} className="text-xs font-medium text-red-600 hover:underline">
            Reject
          </button>
        </div>
      )}
      {control.status === 'active' && (
        <button onClick={() => setMode('deactivate')} className="text-xs font-medium text-red-600 hover:underline">
          Request deactivation
        </button>
      )}
      {control.status === 'pending_deactivation' && (
        <div className="flex justify-end gap-2">
          <button onClick={() => post('deactivation/approve')} disabled={busy} className="text-xs font-medium text-red-600 hover:underline disabled:opacity-60">
            {busy ? 'Approving…' : 'Approve deactivation'}
          </button>
          <button onClick={() => setMode('reject-deactivation')} className="text-xs font-medium text-ink-soft hover:underline">
            Reject
          </button>
        </div>
      )}
      {control.status === 'inactive' && (
        <>
          <button
            onClick={() => post('activation/request')}
            disabled={busy || notReady}
            title={notReady ? `Bind every required table first — still missing: ${missingTables.join(', ')}.` : undefined}
            className="text-xs font-medium text-accent-ink hover:underline disabled:cursor-not-allowed disabled:text-ink-faint disabled:no-underline"
          >
            {busy ? 'Requesting…' : 'Request reactivation'}
          </button>
          {notReady && (
            <p className="mt-0.5 text-[11px] text-ink-faint">
              {progress!.satisfied} of {progress!.total} required tables bound — missing {missingTables.join(', ')}.
            </p>
          )}
        </>
      )}
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  )
}

const CONTROL_STATUS_STYLES: Record<string, string> = {
  pending_mapping: 'bg-amber-100 text-amber-800',
  pending_activation: 'bg-blue-100 text-blue-800 font-bold',
  active: 'bg-emerald-100 text-emerald-800',
  pending_deactivation: 'bg-orange-100 text-orange-800 font-bold',
  inactive: 'bg-bg text-ink-soft',
  retired: 'bg-bg text-ink-soft',
}
// A control sitting in a maker-checker queue needs to stand out in the row
// itself, not just its status pill — an approver scanning a long table
// shouldn't have to read every status cell to find what's waiting on them.
const NEEDS_APPROVAL_STATUSES = new Set(['pending_activation', 'pending_deactivation'])
const CONTROL_STATUS_LABELS: Record<string, string> = {
  pending_mapping: 'Needs table/mapping setup',
  pending_activation: 'Pending activation approval',
  active: 'Active',
  pending_deactivation: 'Pending deactivation approval',
  inactive: 'Deactivated',
  retired: 'Retired',
}

function ControlRow({
  control: c,
  organizationId,
  auditTestId,
  sources,
  progress,
  suggestions,
  onRefreshProgress,
  canManage,
  onChanged,
  riskName,
}: {
  control: ControlOut
  organizationId: string
  auditTestId: string | undefined
  sources: DataSourceOut[]
  progress: TableBindingProgressOut | undefined
  suggestions: Record<string, BindingSuggestion>
  onRefreshProgress: (controlId: string) => void
  canManage: boolean
  onChanged: () => void
  riskName: (id: string) => string
}) {
  const [expanded, setExpanded] = useState(false)

  return (
    <Fragment>
      <tr className={`border-t border-line align-top ${NEEDS_APPROVAL_STATUSES.has(c.status) ? 'bg-orange-50/40' : ''}`}>
        <td className={`px-4 py-2 ${NEEDS_APPROVAL_STATUSES.has(c.status) ? 'font-bold text-ink' : 'font-medium text-ink'}`}>
          {c.control_code ? `${c.control_code} — ` : ''}
          {c.control_name}
        </td>
        <td className="px-4 py-2 text-ink-soft">{c.domain ?? '—'}</td>
        <td className="px-4 py-2">
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${CONTROL_STATUS_STYLES[c.status] ?? ''}`}>
            {CONTROL_STATUS_LABELS[c.status] ?? c.status}
          </span>
        </td>
        <td className="px-4 py-2 text-ink-soft">
          {c.required_tables.length === 0 ? (
            '—'
          ) : (
            <button onClick={() => setExpanded(!expanded)} className="text-left hover:underline">
              {progress ? (
                <span className={progress.ready ? 'text-accent-ink' : 'text-orange-700'}>
                  {progress.satisfied} of {progress.total} tables mapped
                </span>
              ) : (
                c.required_tables.join(', ')
              )}
            </button>
          )}
        </td>
        <td className="px-4 py-2 text-ink-soft">
          {c.risk_ids.length > 0 ? c.risk_ids.map(riskName).join(', ') : '—'}
        </td>
        <td className="px-4 py-2">
          <div className="flex items-center justify-end gap-3">
            <Link to="/audit-tests" className="text-xs font-medium text-accent-ink hover:underline">
              Set up testing →
            </Link>
            {canManage && c.status !== 'retired' && (
              <ControlLifecycleActions control={c} organizationId={organizationId} onChanged={onChanged} progress={progress} />
            )}
          </div>
        </td>
      </tr>
      {expanded && (
        <tr className="border-t border-line bg-bg">
          <td colSpan={6} className="px-6 py-3">
            <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">
              Required tables {c.required_tables.length > 1 && <span className="normal-case text-ink-soft">— map each one below</span>}
            </div>
            <RequiredTablesChecklist
              controlId={c.control_id}
              auditTestId={auditTestId}
              requiredTables={c.required_tables}
              organizationId={organizationId}
              sources={sources}
              progress={progress}
              suggestions={suggestions}
              onRefreshProgress={onRefreshProgress}
              canManage={canManage}
            />
          </td>
        </tr>
      )}
    </Fragment>
  )
}

/**
 * The library browse table's row for one control-library entry. If it's
 * already activated for this org, it gets the exact same expandable
 * required-tables checklist as the Activated tab — an auditor searching
 * the library for a specific control (e.g. "SOD-001") must see live
 * binding state here too, not just on a different tab they may never open.
 */
function LibraryRow({
  entry,
  activeControl,
  organizationId,
  auditTestId,
  sources,
  progress,
  suggestions,
  onRefreshProgress,
  canManage,
  onChanged,
  activatingId,
  handleActivate,
}: {
  entry: ControlLibraryOut
  activeControl: ControlOut | undefined
  organizationId: string | null
  auditTestId: string | undefined
  sources: DataSourceOut[]
  progress: TableBindingProgressOut | undefined
  suggestions: Record<string, BindingSuggestion>
  onRefreshProgress: (controlId: string) => void
  canManage: boolean
  onChanged: () => void
  activatingId: string | null
  handleActivate: (controlLibraryId: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const hasTables = entry.required_tables.length > 0

  return (
    <Fragment>
      <tr className="border-t border-line align-top">
        <td className="px-4 py-2 font-medium text-ink">
          {entry.control_code} — {entry.control_name}
        </td>
        <td className="px-4 py-2 text-ink-soft">{entry.audit_procedure}</td>
        <td className="px-4 py-2 text-ink-soft">
          {!hasTables ? (
            '—'
          ) : activeControl && organizationId ? (
            <button onClick={() => setExpanded(!expanded)} className="text-left hover:underline">
              {progress ? (
                <span className={progress.ready ? 'text-accent-ink' : 'text-orange-700'}>
                  {progress.satisfied} of {progress.total} tables mapped
                </span>
              ) : (
                entry.required_tables.join(', ')
              )}
            </button>
          ) : (
            entry.required_tables.join(', ')
          )}
        </td>
        <td className="px-4 py-2 text-right">
          {activeControl && organizationId ? (
            activeControl.status !== 'retired' && (
              <ControlLifecycleActions control={activeControl} organizationId={organizationId} onChanged={onChanged} progress={progress} />
            )
          ) : (
            <button
              disabled={!organizationId || activatingId === entry.control_library_id}
              onClick={() => handleActivate(entry.control_library_id)}
              className="whitespace-nowrap rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
            >
              {activatingId === entry.control_library_id ? 'Activating…' : 'Activate'}
            </button>
          )}
        </td>
      </tr>
      {expanded && activeControl && organizationId && (
        <tr className="border-t border-line bg-bg">
          <td colSpan={4} className="px-6 py-3">
            <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">
              Required tables {entry.required_tables.length > 1 && <span className="normal-case text-ink-soft">— map each one below</span>}
            </div>
            <RequiredTablesChecklist
              controlId={activeControl.control_id}
              auditTestId={auditTestId}
              requiredTables={entry.required_tables}
              organizationId={organizationId}
              sources={sources}
              progress={progress}
              suggestions={suggestions}
              onRefreshProgress={onRefreshProgress}
              canManage={canManage}
            />
          </td>
        </tr>
      )}
    </Fragment>
  )
}

interface CoverageRow {
  table: string
  totalControls: number
  readyControls: number
}

/**
 * ADDENDUM point 5 — per canonical table, how many activated controls need
 * it and how many already have it satisfied, so the auditor can see which
 * single table to map next unlocks the most controls at once.
 */
function CoverageSummary({
  controls,
  bindingProgress,
}: {
  controls: ControlOut[]
  bindingProgress: Record<string, TableBindingProgressOut>
}) {
  const [open, setOpen] = useState(false)

  const rows = useMemo<CoverageRow[]>(() => {
    const byTable = new Map<string, CoverageRow>()
    for (const c of controls) {
      const progress = bindingProgress[c.control_id]
      for (const table of c.required_tables) {
        const row = byTable.get(table) ?? { table, totalControls: 0, readyControls: 0 }
        row.totalControls += 1
        if (progress?.bindings.some((b) => b.canonical_table_name === table)) row.readyControls += 1
        byTable.set(table, row)
      }
    }
    return [...byTable.values()].sort((a, b) => b.totalControls - b.readyControls - (a.totalControls - a.readyControls))
  }, [controls, bindingProgress])

  if (rows.length === 0) return null
  const unmapped = rows.filter((r) => r.readyControls < r.totalControls)

  return (
    <div className="mt-4 rounded-lg border border-line bg-surface">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between px-4 py-2.5 text-left">
        <span className="text-sm font-medium text-ink">
          Canonical table coverage — {unmapped.length} of {rows.length} tables still block a control
        </span>
        <span className="text-xs text-ink-soft">{open ? 'Hide' : 'Show'}</span>
      </button>
      {open && (
        <div className="border-t border-line px-4 py-3">
          <p className="text-xs text-ink-soft">
            A table used by several controls only has to be mapped once — bind it for whichever control you reach
            first and the rest will suggest the same binding. Mapping the table below with the most controls
            unblocks the most work in one step.
          </p>
          <div className="overflow-x-auto">
            <table className="mt-3 w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
                  <th className="py-1.5 pr-4">Table</th>
                  <th className="py-1.5 pr-4">Controls ready</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.table} className="border-t border-line">
                    <td className="py-1.5 pr-4 font-mono text-xs text-ink">{r.table}</td>
                    <td className="py-1.5 pr-4 text-xs">
                      <span className={r.readyControls === r.totalControls ? 'text-accent-ink' : 'text-orange-700'}>
                        {r.readyControls} of {r.totalControls} controls
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

export function ControlsPage() {
  const { hasRole } = useAuth()
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [controls, setControls] = useState<ControlOut[]>([])
  const [risks, setRisks] = useState<RiskOut[]>([])
  const [library, setLibrary] = useState<ControlLibraryOut[]>([])
  const [view, setView] = useState<'activated' | 'library'>('activated')
  const [search, setSearch] = useState('')
  const [activatingId, setActivatingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dataSources, setDataSources] = useState<DataSourceOut[]>([])
  const [auditTests, setAuditTests] = useState<AuditTestOut[]>([])
  const [bindingProgress, setBindingProgress] = useState<Record<string, TableBindingProgressOut>>({})

  const canManage = hasRole(...AUDIT_FRAMEWORK_ROLES)

  const loadProgressFor = (orgId: string, controlIds: string[]) => {
    Promise.all(
      controlIds.map((id) =>
        apiClient
          .get<TableBindingProgressOut>(`/organizations/${orgId}/controls/${id}/table-bindings`)
          .then((res) => [id, res.data] as const),
      ),
    ).then((entries) => setBindingProgress((prev) => ({ ...prev, ...Object.fromEntries(entries) })))
  }

  // Progress for every activated control is fetched once here (not per-row)
  // so the coverage summary and each control's picker share one source of
  // truth — binding one table refreshes both places at once.
  const refreshProgressFor = (controlId: string) => {
    if (!organizationId) return
    apiClient
      .get<TableBindingProgressOut>(`/organizations/${organizationId}/controls/${controlId}/table-bindings`)
      .then((res) => setBindingProgress((prev) => ({ ...prev, [controlId]: res.data })))
  }

  const load = (orgId: string) => {
    apiClient.get<ControlOut[]>(`/organizations/${orgId}/controls`).then((res) => {
      setControls(res.data)
      loadProgressFor(
        orgId,
        res.data.filter((c) => c.required_tables.length > 0).map((c) => c.control_id),
      )
    })
    apiClient.get<RiskOut[]>(`/organizations/${orgId}/risks`).then((res) => setRisks(res.data))
    apiClient.get<DataSourceOut[]>(`/organizations/${orgId}/data-sources`).then((res) => setDataSources(res.data))
    apiClient.get<AuditTestOut[]>(`/organizations/${orgId}/audit-tests`).then((res) => setAuditTests(res.data))
  }

  useEffect(() => {
    if (organizationId) load(organizationId)
  }, [organizationId])

  const suggestions = useMemo(() => suggestionsFromProgress(bindingProgress), [bindingProgress])

  // A control's test is created automatically on activation, one-to-one in
  // practice — this is how the required-tables checklist knows which audit
  // test to save column mappings against, so binding a table and mapping
  // its columns can happen in the same place instead of two screens.
  const auditTestIdForControl = useMemo(() => {
    const map = new Map<string, string>()
    for (const t of auditTests) {
      for (const controlId of t.control_ids) map.set(controlId, t.audit_test_id)
    }
    return map
  }, [auditTests])

  useEffect(() => {
    apiClient.get<ControlLibraryOut[]>('/control-library').then((res) => setLibrary(res.data))
  }, [])

  const riskName = (id: string) => risks.find((r) => r.risk_id === id)?.risk_name ?? id

  const controlByLibraryId = useMemo(() => {
    const map = new Map<string, ControlOut>()
    for (const c of controls) {
      if (c.control_library_id) map.set(c.control_library_id, c)
    }
    return map
  }, [controls])

  const filteredLibrary = useMemo(() => {
    const term = search.trim().toLowerCase()
    const matches = (c: ControlLibraryOut) =>
      !term ||
      c.control_code.toLowerCase().includes(term) ||
      c.control_name.toLowerCase().includes(term) ||
      c.domain.toLowerCase().includes(term)
    return library.filter(matches)
  }, [library, search])

  const libraryByDomain = useMemo(() => {
    const groups = new Map<string, ControlLibraryOut[]>()
    for (const entry of filteredLibrary) {
      const list = groups.get(entry.domain) ?? []
      list.push(entry)
      groups.set(entry.domain, list)
    }
    return groups
  }, [filteredLibrary])

  const handleActivate = async (controlLibraryId: string) => {
    if (!organizationId) return
    setError(null)
    setActivatingId(controlLibraryId)
    try {
      await apiClient.post(`/organizations/${organizationId}/controls/activate`, { control_library_id: controlLibraryId })
      load(organizationId)
    } catch {
      setError('Could not activate this control — it may already be active.')
    } finally {
      setActivatingId(null)
    }
  }

  const reloadControls = () => {
    if (organizationId) load(organizationId)
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Controls</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Controls come from a pre-built library of {library.length || '150+'} controls across {new Set(library.map((l) => l.domain)).size || 20}{' '}
        audit domains — select one to activate it, confirm its data source tables, then it's ready to test. There's no manual control entry.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-4 flex gap-2 border-b border-line">
        <button
          onClick={() => setView('activated')}
          className={`px-3 py-2 text-sm font-medium ${view === 'activated' ? 'border-b-2 border-accent text-accent-ink' : 'text-ink-soft'}`}
        >
          Activated ({controls.length})
        </button>
        {canManage && (
          <button
            onClick={() => setView('library')}
            className={`px-3 py-2 text-sm font-medium ${view === 'library' ? 'border-b-2 border-accent text-accent-ink' : 'text-ink-soft'}`}
          >
            Browse control library
          </button>
        )}
      </div>

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      {view === 'activated' && organizationId && <CoverageSummary controls={controls} bindingProgress={bindingProgress} />}

      {view === 'activated' && (
        <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
                <th className="px-4 py-2">Control</th>
                <th className="px-4 py-2">Domain</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Required tables</th>
                <th className="px-4 py-2">Linked risks</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {organizationId &&
                controls.map((c) => (
                  <ControlRow
                    key={c.control_id}
                    control={c}
                    organizationId={organizationId}
                    auditTestId={auditTestIdForControl.get(c.control_id)}
                    sources={dataSources}
                    progress={bindingProgress[c.control_id]}
                    suggestions={suggestions}
                    onRefreshProgress={refreshProgressFor}
                    canManage={canManage}
                    onChanged={reloadControls}
                    riskName={riskName}
                  />
                ))}
              {controls.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-6 text-center text-ink-soft">
                    No controls activated yet.{' '}
                    {canManage && (
                      <button onClick={() => setView('library')} className="font-medium text-accent-ink hover:underline">
                        Browse the control library
                      </button>
                    )}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {view === 'library' && canManage && (
        <div className="mt-4">
          <input
            placeholder="Search controls by code, name or domain (e.g. AC-002, terminated employee, payroll)"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full max-w-xl rounded-md border border-line px-3 py-2 text-sm"
          />
          <div className="mt-4 space-y-6">
            {Array.from(libraryByDomain.entries()).map(([domain, entries]) => (
              <div key={domain}>
                <h2 className="text-sm font-semibold text-ink">{domain}</h2>
                <div className="mt-2 overflow-x-auto rounded-lg border border-line bg-surface">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
                        <th className="px-4 py-2">Control</th>
                        <th className="px-4 py-2">Audit procedure</th>
                        <th className="px-4 py-2">Tables required</th>
                        <th className="px-4 py-2" />
                      </tr>
                    </thead>
                    <tbody>
                      {entries.map((entry) => {
                        const activeControl = controlByLibraryId.get(entry.control_library_id)
                        return (
                          <LibraryRow
                            key={entry.control_library_id}
                            entry={entry}
                            activeControl={activeControl}
                            organizationId={organizationId}
                            auditTestId={activeControl ? auditTestIdForControl.get(activeControl.control_id) : undefined}
                            sources={dataSources}
                            progress={activeControl ? bindingProgress[activeControl.control_id] : undefined}
                            suggestions={suggestions}
                            onRefreshProgress={refreshProgressFor}
                            canManage={canManage}
                            onChanged={reloadControls}
                            activatingId={activatingId}
                            handleActivate={handleActivate}
                          />
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
            {filteredLibrary.length === 0 && <p className="text-sm text-ink-soft">No controls match "{search}".</p>}
          </div>
        </div>
      )}
    </div>
  )
}
