import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import type { JoinReportOut, JoinResolutionOut, JoinVerdict } from '../types/api'

const VERDICT_STYLES: Record<JoinVerdict, { box: string; pill: string; label: string }> = {
  valid: { box: 'bg-accent-soft', pill: 'bg-accent text-white', label: 'Proven' },
  ambiguous: { box: 'bg-orange-50', pill: 'bg-orange-100 text-orange-800', label: 'Needs review' },
  contradicted: { box: 'bg-red-50', pill: 'bg-red-100 text-red-800', label: 'Contradicted' },
  unverified: { box: 'bg-bg', pill: 'bg-bg text-ink-soft border border-line', label: 'Not checked' },
  unresolved: { box: 'bg-bg', pill: 'bg-bg text-ink-soft border border-line', label: 'Not mapped yet' },
}

/**
 * The control says which tables must line up and on which fields (e.g.
 * api_access.user_id ↔ user.user_id); this shows how the client's ACTUAL tables
 * satisfy each of those joins and how strongly the schema and the data back it:
 * a declared foreign key, or the values genuinely matching, not just the names.
 * Reads stored evidence (see join_resolution_service) — nothing here queries the
 * client's database; refresh it from Data Sources.
 */
export function RelationshipValidationPanel({ organizationId, auditTestId }: { organizationId: string; auditTestId: string }) {
  const [report, setReport] = useState<JoinReportOut | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [rulingError, setRulingError] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    apiClient
      .get<JoinReportOut>(`/organizations/${organizationId}/audit-tests/${auditTestId}/join-resolution`)
      .then((res) => setReport(res.data))
      .catch((err: any) => setError(err?.response?.data?.detail ?? 'Could not load table relationships.'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [organizationId, auditTestId])

  const rule = async (join: JoinResolutionOut, status: 'confirmed' | 'rejected' | 'detected') => {
    if (!join.relationship_id) return
    setRulingError(null)
    try {
      await apiClient.patch(`/relationships/${join.relationship_id}`, { status })
      load()
    } catch (err: any) {
      setRulingError(err?.response?.data?.detail ?? 'Could not save that decision.')
    }
  }

  const hasContent = report && (report.joins.length > 0 || report.paths.length > 0)

  return (
    <div className="mt-2 rounded-md border border-line bg-surface p-2">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Table relationships</div>
        <button onClick={load} disabled={loading} className="text-xs font-medium text-accent-ink hover:underline disabled:opacity-60">
          {loading ? 'Loading…' : 'Reload'}
        </button>
      </div>
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
      {rulingError && <p className="mt-1 text-xs text-red-600">{rulingError}</p>}
      {report && !hasContent && (
        <p className="mt-1 text-xs text-ink-soft">Nothing to check — this control has no rule template yet, or its test doesn't join two tables.</p>
      )}
      {report?.blocking && (
        <p className="mt-1 rounded-md bg-red-50 px-2 py-1 text-xs text-red-700">
          A join here is contradicted by the data, so the test rule won't be generated automatically until you review it (you can still generate it by hand).
        </p>
      )}
      {report?.joins.map((j, i) => {
        const style = VERDICT_STYLES[j.verdict]
        return (
          <div key={i} className={`mt-1 rounded-md px-2 py-1.5 text-xs ${style.box}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-ink">
                {j.requires_left} <span className="text-ink-soft">↔</span> {j.requires_right}
              </span>
              <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${style.pill}`}>{style.label}</span>
              {j.relationship === 'declared_fk' && <span className="text-[11px] text-ink-soft">declared foreign key</span>}
            </div>
            {j.left && j.right && (
              <div className="mt-0.5 font-mono text-ink-soft">
                {j.left.table}.{j.left.column} → {j.right.table}.{j.right.column}
              </div>
            )}
            <div className="mt-0.5 text-ink-soft">{j.reason}</div>
            {j.evidence.filter((e) => e !== j.reason).map((e, k) => (
              <div key={k} className="text-ink-soft">
                · {e}
              </div>
            ))}
            {j.alternatives.length > 0 && (
              <div className="mt-0.5 text-orange-700">
                Also possible: {j.alternatives.map((a) => `${a.left.table}.${a.left.column} ↔ ${a.right.table}.${a.right.column}`).join('; ')}
              </div>
            )}
            {j.relationship_id && j.relationship !== 'declared_fk' && (
              <div className="mt-1 flex gap-3">
                <button onClick={() => rule(j, 'confirmed')} className="font-medium text-accent-ink hover:underline">
                  Confirm this relationship
                </button>
                <button onClick={() => rule(j, 'rejected')} className="font-medium text-red-700 hover:underline">
                  Reject it
                </button>
              </div>
            )}
          </div>
        )
      })}
      {report && report.paths.length > 0 && (
        <div className="mt-2">
          <div className="text-[11px] font-medium uppercase tracking-wide text-ink-soft">How this control's tables connect</div>
          {report.paths.map((p, i) => (
            <div key={i} className="mt-1 rounded-md bg-bg px-2 py-1 text-xs">
              <div className="font-medium text-ink">
                {p.from_table} → {p.to_table}
              </div>
              <div className="font-mono text-ink-soft">{p.steps.map((s) => `${s.from_column} = ${s.to_column}`).join('  ·  ')}</div>
            </div>
          ))}
          <p className="mt-1 text-[11px] text-ink-soft">Shown for understanding: tests still join two tables directly, so a path through a bridge table is not executed.</p>
        </div>
      )}
    </div>
  )
}
