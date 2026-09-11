import type { OrganizationOut } from '../types/api'

interface Props {
  organizations: OrganizationOut[]
  value: string | null
  onChange: (organizationId: string) => void
}

export function OrganizationPicker({ organizations, value, onChange }: Props) {
  return (
    <div className="mb-4">
      <label className="text-xs font-medium uppercase tracking-wide text-ink-soft">Organization</label>
      <select
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 block rounded-md border border-line px-3 py-2 text-sm"
      >
        {organizations.map((org) => (
          <option key={org.organization_id} value={org.organization_id}>
            {org.organization_name}
          </option>
        ))}
      </select>
    </div>
  )
}
