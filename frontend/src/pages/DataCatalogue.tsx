import { Fragment, useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { DataEntityOut, DataFieldOut, DataSourceOut } from '../types/api'

export function DataCataloguePage() {
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [entities, setEntities] = useState<DataEntityOut[]>([])
  const [sources, setSources] = useState<DataSourceOut[]>([])
  const [expandedEntityId, setExpandedEntityId] = useState<string | null>(null)
  const [fields, setFields] = useState<DataFieldOut[]>([])

  useEffect(() => {
    if (!organizationId) return
    apiClient.get<DataEntityOut[]>(`/organizations/${organizationId}/data-catalogue`).then((res) => setEntities(res.data))
    apiClient.get<DataSourceOut[]>(`/organizations/${organizationId}/data-sources`).then((res) => setSources(res.data))
  }, [organizationId])

  const sourceName = (id: string) => sources.find((s) => s.data_source_id === id)?.source_name ?? id

  const toggleExpand = (entityId: string) => {
    if (expandedEntityId === entityId) {
      setExpandedEntityId(null)
      return
    }
    setExpandedEntityId(entityId)
    apiClient.get<DataFieldOut[]>(`/entities/${entityId}/fields`).then((res) => setFields(res.data))
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Data Catalogue</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Every table discovered across this organization's data sources by its Gateway(s). Expand a table to see its fields —
        mapping a field to the canonical audit model happens on the Audit Tests page.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Table</th>
              <th className="px-4 py-2">Data source</th>
              <th className="px-4 py-2">Type</th>
              <th className="px-4 py-2">Description</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {entities.map((e) => (
              <Fragment key={e.entity_id}>
                <tr className="border-t border-line">
                  <td className="px-4 py-2 font-medium text-ink">{e.entity_name}</td>
                  <td className="px-4 py-2 text-ink-soft">{sourceName(e.data_source_id)}</td>
                  <td className="px-4 py-2 text-ink-soft">{e.entity_type}</td>
                  <td className="px-4 py-2 text-ink-soft">{e.description ?? '—'}</td>
                  <td className="px-4 py-2 text-right">
                    <button onClick={() => toggleExpand(e.entity_id)} className="text-xs font-medium text-accent-ink hover:underline">
                      {expandedEntityId === e.entity_id ? 'Hide fields' : 'Show fields'}
                    </button>
                  </td>
                </tr>
                {expandedEntityId === e.entity_id && (
                  <tr>
                    <td colSpan={5} className="bg-bg px-6 py-3">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="text-left uppercase tracking-wide text-ink-soft">
                            <th className="py-1 pr-4">Field</th>
                            <th className="py-1 pr-4">Data type</th>
                            <th className="py-1 pr-4">Primary key</th>
                            <th className="py-1">Sensitive</th>
                          </tr>
                        </thead>
                        <tbody>
                          {fields.map((f) => (
                            <tr key={f.field_id} className="border-t border-line">
                              <td className="py-1 pr-4 font-mono text-ink">{f.field_name}</td>
                              <td className="py-1 pr-4 text-ink-soft">{f.data_type ?? '—'}</td>
                              <td className="py-1 pr-4 text-ink-soft">{f.is_primary_key ? 'Yes' : '—'}</td>
                              <td className="py-1 text-ink-soft">{f.is_sensitive ? 'Yes' : '—'}</td>
                            </tr>
                          ))}
                          {fields.length === 0 && (
                            <tr>
                              <td colSpan={4} className="py-2 text-center text-ink-soft">
                                No fields discovered for this table yet.
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {entities.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-ink-soft">
                  No tables discovered yet — a Gateway reports these automatically once a connection tests successfully.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
