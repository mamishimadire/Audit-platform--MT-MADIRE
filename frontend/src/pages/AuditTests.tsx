import { Fragment, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../lib/apiClient'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import { DataMappingPanel } from '../components/DataMappingPanel'
import { TestEnginePanel } from '../components/TestEnginePanel'
import type { AuditTestOut } from '../types/api'

const STATUS_STYLES: Record<string, string> = {
  draft: 'bg-bg text-ink-soft',
  pending_approval: 'bg-orange-50 text-orange-700',
  active: 'bg-accent-soft text-accent-ink',
  disabled: 'bg-bg text-ink-soft',
}

// The test's own lifecycle status (above) says nothing about whether its
// DATA is mapped, let alone whether that mapping has actually been signed
// off — a test can sit at "draft" indefinitely with its mapping fully
// approved, or "active" with mappings still awaiting a second person.
// This is the separate, maker-checker-flow status for that.
const MAPPING_STATUS_LABELS: Record<string, string> = {
  not_mapped: 'Not mapped',
  pending_approval: 'Mapped — awaiting approval',
  rejected: 'Mapping rejected',
  approved: 'Mapped & approved',
}

const MAPPING_STATUS_STYLES: Record<string, string> = {
  not_mapped: 'bg-bg text-ink-soft',
  pending_approval: 'bg-orange-50 text-orange-700',
  rejected: 'bg-red-50 text-red-700',
  approved: 'bg-accent-soft text-accent-ink',
}

export function AuditTestsPage() {
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [tests, setTests] = useState<AuditTestOut[]>([])
  const [expandedTestId, setExpandedTestId] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  const load = (orgId: string) => {
    apiClient.get<AuditTestOut[]>(`/organizations/${orgId}/audit-tests`).then((res) => setTests(res.data))
  }

  useEffect(() => {
    if (organizationId) load(organizationId)
  }, [organizationId])

  const filteredTests = useMemo(() => {
    const term = search.trim().toLowerCase()
    if (!term) return tests
    return tests.filter(
      (t) =>
        (t.test_code ?? '').toLowerCase().includes(term) ||
        t.test_name.toLowerCase().includes(term) ||
        (t.domain ?? '').toLowerCase().includes(term)
    )
  }, [tests, search])

  // Same grouping the Controls page uses for its own activated controls —
  // a test is auto-created one-to-one with its control, in the same
  // category, so this groups the same way for the same reason: a long flat
  // list otherwise mixes every category together with nothing to scan or
  // search by.
  const testsByDomain = useMemo(() => {
    const groups = new Map<string, AuditTestOut[]>()
    for (const t of filteredTests) {
      const domain = t.domain ?? 'Uncategorized'
      const list = groups.get(domain) ?? []
      list.push(t)
      groups.set(domain, list)
    }
    return new Map([...groups.entries()].sort(([a], [b]) => a.localeCompare(b)))
  }, [filteredTests])

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Audit Tests</h1>
      <p className="mt-1 text-sm text-ink-soft">
        A test is created automatically, in the same category as its control, the moment you activate that control — there's nothing to
        build by hand here. Use <strong>Map data</strong> to link fields from a discovered table to the canonical audit model.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <input
        placeholder="Search tests by code, name or domain"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="mt-4 w-full max-w-xl rounded-md border border-line px-3 py-2 text-sm"
      />

      <div className="mt-4 space-y-6">
        {Array.from(testsByDomain.entries()).map(([domain, entries]) => (
          <div key={domain}>
            <h2 className="text-sm font-semibold text-ink">
              {domain} <span className="font-normal text-ink-faint">({entries.length})</span>
            </h2>
            <div className="mt-2 overflow-x-auto rounded-lg border border-line bg-surface">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
                    <th className="px-4 py-2">Test</th>
                    <th className="px-4 py-2">Frequency</th>
                    <th className="px-4 py-2">Status</th>
                    <th className="px-4 py-2">Mapping</th>
                    <th className="px-4 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((t) => (
                    <Fragment key={t.audit_test_id}>
                      <tr className="border-t border-line">
                        <td className="px-4 py-2 font-medium text-ink">
                          {t.test_code ? `${t.test_code} — ` : ''}
                          {t.test_name}
                        </td>
                        <td className="px-4 py-2 text-ink-soft">{t.frequency ?? '—'}</td>
                        <td className="px-4 py-2">
                          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[t.status] ?? ''}`}>
                            {t.status}
                          </span>
                        </td>
                        <td className="px-4 py-2">
                          {t.test_type === 'endpoint_compliance' ? (
                            <span className="rounded-full bg-bg px-2 py-0.5 text-xs font-medium text-ink-soft">Not applicable</span>
                          ) : (
                            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${MAPPING_STATUS_STYLES[t.mapping_status] ?? ''}`}>
                              {MAPPING_STATUS_LABELS[t.mapping_status] ?? t.mapping_status}
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-2 text-right">
                          <button
                            onClick={() => setExpandedTestId(expandedTestId === t.audit_test_id ? null : t.audit_test_id)}
                            className="text-xs font-medium text-accent-ink hover:underline"
                          >
                            {expandedTestId === t.audit_test_id
                              ? 'Hide details'
                              : t.test_type === 'endpoint_compliance'
                                ? 'Details'
                                : 'Map data'}
                          </button>
                        </td>
                      </tr>
                      {expandedTestId === t.audit_test_id && organizationId && (
                        <tr>
                          <td colSpan={5} className="space-y-px p-0">
                            {t.test_type === 'endpoint_compliance' ? (
                              <p className="rounded-md border border-line bg-surface px-3 py-2 text-xs text-ink-soft">
                                This test runs automatically in real time whenever a device reports in — it's evaluated by its
                                own built-in logic, not the generic mapping/rule engine, so there's no data to map, no rule to
                                generate, and no schedule to set here.
                              </p>
                            ) : (
                              <>
                                <DataMappingPanel
                                  organizationId={organizationId}
                                  auditTestId={t.audit_test_id}
                                  controlId={t.control_ids[0] ?? null}
                                  requiredTables={t.required_tables}
                                />
                                <TestEnginePanel organizationId={organizationId} auditTestId={t.audit_test_id} />
                              </>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
        {tests.length === 0 && (
          <p className="rounded-lg border border-line bg-surface px-4 py-6 text-center text-sm text-ink-soft">
            No audit tests yet — activate a control from the{' '}
            <Link to="/controls" className="font-medium text-accent-ink hover:underline">
              Controls
            </Link>{' '}
            library and its test appears here automatically.
          </p>
        )}
        {tests.length > 0 && filteredTests.length === 0 && (
          <p className="rounded-lg border border-line bg-surface px-4 py-6 text-center text-sm text-ink-soft">No audit tests match "{search}".</p>
        )}
      </div>
    </div>
  )
}
