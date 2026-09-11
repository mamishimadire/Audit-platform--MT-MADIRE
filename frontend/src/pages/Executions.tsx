import { Fragment, useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { AuditTestOut, ExceptionOut, TestExecutionOut } from '../types/api'

const STATUS_STYLES: Record<string, string> = {
  completed: 'bg-accent-soft text-accent-ink',
  running: 'bg-bg text-ink-soft',
  failed: 'bg-red-50 text-red-700',
}

const STATUS_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'failed', label: 'Failed' },
  { key: 'completed', label: 'Completed' },
  { key: 'running', label: 'Running' },
] as const
type StatusFilterKey = (typeof STATUS_FILTERS)[number]['key']

export function ExecutionsPage() {
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [executions, setExecutions] = useState<TestExecutionOut[]>([])
  const [tests, setTests] = useState<AuditTestOut[]>([])
  const [exceptions, setExceptions] = useState<ExceptionOut[]>([])
  const [filter, setFilter] = useState<StatusFilterKey>('all')
  const [expandedId, setExpandedId] = useState<string | null>(null)

  useEffect(() => {
    if (!organizationId) return
    apiClient.get<TestExecutionOut[]>(`/organizations/${organizationId}/executions`).then((res) => setExecutions(res.data))
    apiClient.get<AuditTestOut[]>(`/organizations/${organizationId}/audit-tests`).then((res) => setTests(res.data))
    apiClient.get<ExceptionOut[]>(`/organizations/${organizationId}/exceptions`).then((res) => setExceptions(res.data))
  }, [organizationId])

  const testLabel = (id: string) => {
    const t = tests.find((t) => t.audit_test_id === id)
    return t ? (t.test_code ? `${t.test_code} — ${t.test_name}` : t.test_name) : id
  }

  // The reason a run failed is simply the exceptions it produced — every
  // execution path (SQL-backed audit rule, endpoint check, software policy
  // check) already creates real Exception rows linked by execution_id, so
  // this reuses that instead of parsing each test type's own evidence shape.
  const reasonsFor = (executionId: string) => exceptions.filter((e) => e.execution_id === executionId).map((e) => e.exception_description).filter(Boolean) as string[]

  const sorted = [...executions].sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime())

  const counts = {
    all: sorted.length,
    failed: sorted.filter((e) => e.status === 'failed').length,
    completed: sorted.filter((e) => e.status === 'completed').length,
    running: sorted.filter((e) => e.status === 'running').length,
  }

  const filtered = sorted.filter((e) => filter === 'all' || e.status === filter)

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Executions</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Every run of every audit test in this organization, newest first — a failed test is always recorded as failed,
        never silently reinterpreted as a pass.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-4 flex gap-1 rounded-md bg-bg p-0.5 text-xs w-fit">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`rounded px-3 py-1.5 font-medium ${filter === f.key ? 'bg-surface text-ink shadow-sm' : 'text-ink-soft'}`}
          >
            {f.label} <span className="text-ink-faint">{counts[f.key]}</span>
          </button>
        ))}
      </div>

      <div className="mt-3 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Audit test</th>
              <th className="px-4 py-2">Date and time</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Records analyzed</th>
              <th className="px-4 py-2">Exceptions</th>
              <th className="px-4 py-2">Reason for failure</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((e) => {
              const reasons = e.status === 'failed' ? reasonsFor(e.execution_id) : []
              const isExpanded = expandedId === e.execution_id
              const preview = reasons.slice(0, 2).join('; ')
              return (
                <Fragment key={e.execution_id}>
                  <tr className="border-t border-line align-top">
                    <td className="px-4 py-2 font-medium text-ink">{testLabel(e.audit_test_id)}</td>
                    <td className="px-4 py-2 text-ink-soft whitespace-nowrap">{new Date(e.started_at).toLocaleString()}</td>
                    <td className="px-4 py-2">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[e.status] ?? ''}`}>{e.status}</span>
                    </td>
                    <td className="px-4 py-2 text-ink-soft">{e.records_analyzed ?? '—'}</td>
                    <td className="px-4 py-2 text-ink-soft">{e.exceptions_found ?? '—'}</td>
                    <td className="px-4 py-2 text-ink-soft max-w-md">
                      {reasons.length === 0 ? (
                        e.status === 'failed' ? 'No matching exception record found.' : '—'
                      ) : (
                        <>
                          {preview}
                          {reasons.length > 2 && (
                            <button
                              onClick={() => setExpandedId(isExpanded ? null : e.execution_id)}
                              className="ml-1 font-medium text-accent-ink hover:underline"
                            >
                              {isExpanded ? 'hide' : `+${reasons.length - 2} more`}
                            </button>
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                  {isExpanded && reasons.length > 2 && (
                    <tr className="border-t border-line-soft bg-bg">
                      <td colSpan={6} className="px-4 py-2">
                        <ul className="list-disc space-y-0.5 pl-5 text-xs text-ink-soft">
                          {reasons.map((r, i) => (
                            <li key={i}>{r}</li>
                          ))}
                        </ul>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-ink-soft">
                  No test executions{filter !== 'all' ? ` with status "${filter}"` : ''} yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
