import { useState } from 'react'
import { apiClient } from '../lib/apiClient'
import type { RulePreviewOut } from '../types/api'

/**
 * "Show the auditor exactly what will be tested before they activate a
 * control" — a plain-English SOURCE/JOIN/FILTER/TEST/PASS breakdown, built
 * from whichever rule is most relevant right now (active, else pending
 * approval, else the control's own template) so it can be checked before
 * "Generate from control template" is even clicked.
 */
export function RulePreview({ organizationId, auditTestId }: { organizationId: string; auditTestId: string }) {
  const [preview, setPreview] = useState<RulePreviewOut | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)

  const run = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await apiClient.get<RulePreviewOut>(
        `/organizations/${organizationId}/audit-tests/${auditTestId}/rule-preview`,
      )
      setPreview(res.data)
      setOpen(true)
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'No rule or control template exists yet for this test.')
      setOpen(true)
    } finally {
      setLoading(false)
    }
  }

  if (!open) {
    return (
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <button
          onClick={run}
          disabled={loading}
          className="rounded-md border border-line px-2.5 py-1 font-medium text-ink hover:bg-bg disabled:opacity-60"
        >
          {loading ? 'Loading…' : 'Preview what this rule tests'}
        </button>
        <span className="text-ink-soft">See it in plain English before you trust the result.</span>
      </div>
    )
  }

  return (
    <div className="mt-2 rounded-md border border-line bg-surface p-2">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Rule preview</div>
        <div className="flex items-center gap-2">
          <button onClick={run} disabled={loading} className="text-xs font-medium text-accent-ink hover:underline disabled:opacity-60">
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
          <button onClick={() => setOpen(false)} className="text-xs font-medium text-ink-soft hover:underline">
            Hide
          </button>
        </div>
      </div>

      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}

      {preview && (
        <div className="mt-2 space-y-1.5 text-xs">
          <div>
            <span className="font-medium text-ink-soft">SOURCE </span>
            <span className="font-mono text-ink">{preview.source}</span>
          </div>
          {preview.joins.length > 0 && (
            <div>
              <span className="font-medium text-ink-soft">JOIN </span>
              {preview.joins.map((j, i) => (
                <div key={i} className="ml-2 font-mono text-ink">
                  {j}
                </div>
              ))}
            </div>
          )}
          {preview.filters.length > 0 && (
            <div>
              <span className="font-medium text-ink-soft">FILTER </span>
              {preview.filters.map((f, i) => (
                <div key={i} className="ml-2 font-mono text-ink">
                  {f}
                </div>
              ))}
            </div>
          )}
          <div>
            <span className="font-medium text-ink-soft">TEST </span>
            <span className="text-ink">{preview.test_condition}</span>
          </div>
          <div className="rounded-md bg-accent-soft px-2 py-1 text-accent-ink">
            <span className="font-medium">PASS </span>
            {preview.pass_condition}
          </div>

          {preview.field_mappings.length > 0 && (
            <div className="mt-2">
              <div className="text-[10px] font-medium uppercase tracking-wide text-ink-soft">
                Fields this rule reads
              </div>
              <ul className="mt-1 space-y-0.5">
                {preview.field_mappings.map((fm) => (
                  <li key={fm.canonical} className="flex items-center gap-1.5 font-mono">
                    <span className={fm.mapped ? 'text-ink-soft' : 'text-red-600'}>{fm.canonical}</span>
                    <span className="text-ink-soft">→</span>
                    <span className={fm.mapped ? 'text-ink' : 'text-red-600'}>{fm.physical}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
