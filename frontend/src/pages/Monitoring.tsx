import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { AuditTestOut, MonitoringScheduleOut } from '../types/api'

export function MonitoringPage() {
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [schedules, setSchedules] = useState<MonitoringScheduleOut[]>([])
  const [tests, setTests] = useState<AuditTestOut[]>([])

  useEffect(() => {
    if (!organizationId) return
    apiClient.get<MonitoringScheduleOut[]>(`/organizations/${organizationId}/schedules`).then((res) => setSchedules(res.data))
    apiClient.get<AuditTestOut[]>(`/organizations/${organizationId}/audit-tests`).then((res) => setTests(res.data))
  }, [organizationId])

  const testLabel = (id: string) => {
    const t = tests.find((t) => t.audit_test_id === id)
    return t ? (t.test_code ? `${t.test_code} — ${t.test_name}` : t.test_name) : id
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Monitoring</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Every continuous-monitoring schedule across this organization's audit tests. Add or change a schedule from the
        Audit Tests page — expand a test and use "Map data" to reach its schedule.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Audit test</th>
              <th className="px-4 py-2">Frequency</th>
              <th className="px-4 py-2">Last run</th>
              <th className="px-4 py-2">Next run</th>
              <th className="px-4 py-2">Active</th>
            </tr>
          </thead>
          <tbody>
            {schedules.map((s) => (
              <tr key={s.schedule_id} className="border-t border-line">
                <td className="px-4 py-2 font-medium text-ink">{testLabel(s.audit_test_id)}</td>
                <td className="px-4 py-2 text-ink-soft">{s.frequency}</td>
                <td className="px-4 py-2 text-ink-soft">{s.last_run ? new Date(s.last_run).toLocaleString() : 'never'}</td>
                <td className="px-4 py-2 text-ink-soft">{s.next_run ? new Date(s.next_run).toLocaleString() : '—'}</td>
                <td className="px-4 py-2">
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                      s.is_active ? 'bg-accent-soft text-accent-ink' : 'bg-bg text-ink-soft'
                    }`}
                  >
                    {s.is_active ? 'active' : 'paused'}
                  </span>
                </td>
              </tr>
            ))}
            {schedules.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-ink-soft">
                  No monitoring schedules yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
