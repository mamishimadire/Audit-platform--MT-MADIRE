import { useState } from 'react'
import { apiClient } from '../lib/apiClient'
import type { RelationshipCheckOut } from '../types/api'

const STATUS_STYLES: Record<string, string> = {
  validated: 'bg-accent-soft',
  weak: 'bg-red-50',
  not_available: 'bg-bg',
}
const DETAIL_STYLES: Record<string, string> = {
  validated: 'text-accent-ink',
  weak: 'text-red-700',
  not_available: 'text-ink-soft',
}

/**
 * A join_field mapped correctly by NAME doesn't guarantee the two sides
 * actually share the same values — this runs a live query against the
 * client's own data (see relationship_validation_service.py) to check how
 * much they genuinely overlap. Advisory, not a hard gate: real data is
 * often legitimately messy, so a weak result is surfaced prominently for
 * the auditor to judge rather than silently blocking activation.
 */
export function RelationshipValidationPanel({ organizationId, auditTestId }: { organizationId: string; auditTestId: string }) {
  const [checks, setChecks] = useState<RelationshipCheckOut[] | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setRunning(true)
    setError(null)
    try {
      const res = await apiClient.post<RelationshipCheckOut[]>(
        `/organizations/${organizationId}/audit-tests/${auditTestId}/relationship-validation`,
      )
      setChecks(res.data)
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not run relationship validation.')
    } finally {
      setRunning(false)
    }
  }

  if (checks === null && !running) {
    return (
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <button onClick={run} disabled={running} className="rounded-md border border-line px-2.5 py-1 font-medium text-ink hover:bg-bg disabled:opacity-60">
          Validate relationships
        </button>
        <span className="text-ink-soft">Checks whether the mapped join fields actually share values, not just names.</span>
        {error && <span className="text-red-600">{error}</span>}
      </div>
    )
  }

  return (
    <div className="mt-2 rounded-md border border-line bg-surface p-2">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Relationship validation</div>
        <button onClick={run} disabled={running} className="text-xs font-medium text-accent-ink hover:underline disabled:opacity-60">
          {running ? 'Checking…' : 'Re-check'}
        </button>
      </div>
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
      {checks && checks.length === 0 && (
        <p className="mt-1 text-xs text-ink-soft">
          Nothing to validate — this control has no rule template yet, or its test doesn't join two tables.
        </p>
      )}
      {checks?.map((c, i) => (
        <div key={i} className={`mt-1 rounded-md px-2 py-1.5 text-xs ${STATUS_STYLES[c.status]}`}>
          <div className="font-mono text-ink">
            {c.primary_object}.{c.join_field} <span className="text-ink-soft">↔</span> {c.secondary_object}.{c.secondary_join_field}
          </div>
          <div className={DETAIL_STYLES[c.status]}>
            {c.status === 'not_available' ? c.detail : `${c.match_rate}% match — ${c.detail}`}
          </div>
        </div>
      ))}
    </div>
  )
}
