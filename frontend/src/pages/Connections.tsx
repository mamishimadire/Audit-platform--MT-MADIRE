import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { DataConnectionOut, DataSourceOut, GatewayOut } from '../types/api'

const STATUS_STYLES: Record<string, string> = {
  connected: 'bg-accent-soft text-accent-ink',
  pending: 'bg-bg text-ink-soft',
  failed: 'bg-red-50 text-red-700',
}

export function ConnectionsPage() {
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [connections, setConnections] = useState<DataConnectionOut[]>([])
  const [sources, setSources] = useState<DataSourceOut[]>([])
  const [gateways, setGateways] = useState<GatewayOut[]>([])

  useEffect(() => {
    if (!organizationId) return
    apiClient.get<DataConnectionOut[]>(`/organizations/${organizationId}/connections`).then((res) => setConnections(res.data))
    apiClient.get<DataSourceOut[]>(`/organizations/${organizationId}/data-sources`).then((res) => setSources(res.data))
    apiClient.get<GatewayOut[]>(`/organizations/${organizationId}/gateways`).then((res) => setGateways(res.data))
  }, [organizationId])

  const sourceName = (id: string) => sources.find((s) => s.data_source_id === id)?.source_name ?? id
  const gatewayName = (id: string | null) => (id ? gateways.find((g) => g.gateway_id === id)?.gateway_name ?? id : '—')

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Connections</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Every connection across all of this organization's data sources, with the Gateway that reaches it and when it was
        last tested. Add a new one from the Data Sources page.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Data source</th>
              <th className="px-4 py-2">Gateway</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Last tested</th>
            </tr>
          </thead>
          <tbody>
            {connections.map((c) => (
              <tr key={c.connection_id} className="border-t border-line">
                <td className="px-4 py-2 font-medium text-ink">{sourceName(c.data_source_id)}</td>
                <td className="px-4 py-2 text-ink-soft">{gatewayName(c.gateway_id)}</td>
                <td className="px-4 py-2">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[c.connection_status] ?? ''}`}>
                    {c.connection_status}
                  </span>
                </td>
                <td className="px-4 py-2 text-ink-soft">{c.last_tested_at ? new Date(c.last_tested_at).toLocaleString() : 'never'}</td>
              </tr>
            ))}
            {connections.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-ink-soft">
                  No connections yet — register a data source and add a connection there.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
