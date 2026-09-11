import { Fragment, useEffect, useState } from 'react'
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

export function AuditTestsPage() {
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [tests, setTests] = useState<AuditTestOut[]>([])
  const [expandedTestId, setExpandedTestId] = useState<string | null>(null)

  const load = (orgId: string) => {
    apiClient.get<AuditTestOut[]>(`/organizations/${orgId}/audit-tests`).then((res) => setTests(res.data))
  }

  useEffect(() => {
    if (organizationId) load(organizationId)
  }, [organizationId])

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Audit Tests</h1>
      <p className="mt-1 text-sm text-ink-soft">
        A test is created automatically, in the same category as its control, the moment you activate that control — there's nothing to
        build by hand here. Use <strong>Map data</strong> to link fields from a discovered table to the canonical audit model.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Test</th>
              <th className="px-4 py-2">Domain</th>
              <th className="px-4 py-2">Frequency</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {tests.map((t) => (
              <Fragment key={t.audit_test_id}>
                <tr className="border-t border-line">
                  <td className="px-4 py-2 font-medium text-ink">
                    {t.test_code ? `${t.test_code} — ` : ''}
                    {t.test_name}
                  </td>
                  <td className="px-4 py-2 text-ink-soft">{t.domain ?? '—'}</td>
                  <td className="px-4 py-2 text-ink-soft">{t.frequency ?? '—'}</td>
                  <td className="px-4 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[t.status] ?? ''}`}>
                      {t.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">
                    <button
                      onClick={() => setExpandedTestId(expandedTestId === t.audit_test_id ? null : t.audit_test_id)}
                      className="text-xs font-medium text-accent-ink hover:underline"
                    >
                      {expandedTestId === t.audit_test_id ? 'Hide mapping' : 'Map data'}
                    </button>
                  </td>
                </tr>
                {expandedTestId === t.audit_test_id && organizationId && (
                  <tr>
                    <td colSpan={5} className="space-y-px p-0">
                      <DataMappingPanel
                        organizationId={organizationId}
                        auditTestId={t.audit_test_id}
                        controlId={t.control_ids[0] ?? null}
                        requiredTables={t.required_tables}
                      />
                      <TestEnginePanel organizationId={organizationId} auditTestId={t.audit_test_id} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {tests.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-ink-soft">
                  No audit tests yet — activate a control from the{' '}
                  <Link to="/controls" className="font-medium text-accent-ink hover:underline">
                    Controls
                  </Link>{' '}
                  library and its test appears here automatically.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
