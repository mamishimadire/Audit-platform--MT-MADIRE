import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import type { IndustryOut, OrganizationCreatedOut, OrganizationOut } from '../types/api'

const emptyForm = {
  organization_name: '',
  trading_name: '',
  industry_ids: [] as string[],
  country: '',
  primary_admin_first_name: '',
  primary_admin_last_name: '',
  primary_admin_email: '',
}

export function OrganizationsPage() {
  const { hasRole } = useAuth()
  const [organizations, setOrganizations] = useState<OrganizationOut[]>([])
  const [industries, setIndustries] = useState<IndustryOut[]>([])
  const [industrySearch, setIndustrySearch] = useState('')
  const [form, setForm] = useState(emptyForm)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [justCreated, setJustCreated] = useState<OrganizationCreatedOut | null>(null)
  const [copied, setCopied] = useState(false)

  const canCreate = hasRole('Platform Super Admin', 'Platform Admin')

  const load = () => apiClient.get<OrganizationOut[]>('/organizations').then((res) => setOrganizations(res.data))

  useEffect(() => {
    load()
    apiClient.get<IndustryOut[]>('/reference/industries').then((res) => setIndustries(res.data))
  }, [])

  const filteredIndustries = useMemo(() => {
    const term = industrySearch.trim().toLowerCase()
    if (!term) return industries
    return industries.filter((i) => i.industry_name.toLowerCase().includes(term))
  }, [industries, industrySearch])

  const industryName = (id: string) => industries.find((i) => i.industry_id === id)?.industry_name ?? id

  const toggleIndustry = (industryId: string) => {
    setForm((f) => ({
      ...f,
      industry_ids: f.industry_ids.includes(industryId)
        ? f.industry_ids.filter((id) => id !== industryId)
        : [...f.industry_ids, industryId],
    }))
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setJustCreated(null)
    setIsSubmitting(true)
    try {
      const res = await apiClient.post<OrganizationCreatedOut>('/organizations', form)
      setForm(emptyForm)
      setJustCreated(res.data)
      load()
    } catch {
      setError('Could not create the organization — check the fields and try again.')
    } finally {
      setIsSubmitting(false)
    }
  }

  const copyPassword = async () => {
    if (!justCreated) return
    try {
      await navigator.clipboard.writeText(justCreated.temporary_password)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard access denied — the password is still visible to select manually
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Organizations</h1>

      {justCreated && (
        <div className="mt-4 rounded-lg border border-accent-soft bg-accent-soft p-4 text-sm">
          <div className="font-semibold text-accent-ink">
            {justCreated.organization_name} created — one-time temporary password for {justCreated.primary_admin_email}
          </div>
          <p className="mt-1 text-ink-soft">
            Copy this and send it to them yourself (e.g. by email). It also stays visible on the{' '}
            <Link to="/users" className="font-medium underline">
              Users
            </Link>{' '}
            page until they activate their account. Its starting risk register was auto-created from the selected industries —
            see{' '}
            <Link to="/risks" className="font-medium underline">
              Risks
            </Link>
            .
          </p>
          <div className="mt-2 flex items-center gap-2">
            <code className="flex-1 overflow-x-auto rounded-md bg-surface px-3 py-2 font-mono text-sm text-ink">
              {justCreated.temporary_password}
            </code>
            <button onClick={copyPassword} className="whitespace-nowrap rounded-md bg-accent px-3 py-2 text-xs font-medium text-white">
              {copied ? 'Copied ✓' : 'Copy'}
            </button>
          </div>
          <button onClick={() => setJustCreated(null)} className="mt-2 text-xs font-medium text-accent-ink hover:underline">
            Dismiss
          </button>
        </div>
      )}

      <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-soft">
              <th className="px-4 py-2">Name</th>
              <th className="px-4 py-2">Industries</th>
              <th className="px-4 py-2">Country</th>
              <th className="px-4 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {organizations.map((org) => (
              <tr key={org.organization_id} className="border-t border-line">
                <td className="px-4 py-2 font-medium text-ink">{org.organization_name}</td>
                <td className="px-4 py-2 text-ink-soft">{org.industries.length > 0 ? org.industries.join(', ') : '—'}</td>
                <td className="px-4 py-2 text-ink-soft">{org.country ?? '—'}</td>
                <td className="px-4 py-2 text-ink-soft">{org.status}</td>
              </tr>
            ))}
            {organizations.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-ink-soft">
                  No organizations yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {canCreate && (
        <form onSubmit={handleSubmit} className="mt-6 max-w-xl rounded-lg border border-line bg-surface p-5">
          <h2 className="text-sm font-semibold text-ink">Add a new client organization</h2>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <input
              required
              placeholder="Organization name"
              value={form.organization_name}
              onChange={(e) => setForm({ ...form, organization_name: e.target.value })}
              className="col-span-2 rounded-md border border-line px-3 py-2 text-sm"
            />
            <input
              placeholder="Trading name (optional)"
              value={form.trading_name}
              onChange={(e) => setForm({ ...form, trading_name: e.target.value })}
              className="col-span-2 rounded-md border border-line px-3 py-2 text-sm"
            />

            <div className="col-span-2">
              <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">
                Industries — select one or more, this auto-creates the starting risk register
              </div>
              {form.industry_ids.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {form.industry_ids.map((id) => (
                    <span key={id} className="flex items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 text-xs text-accent-ink">
                      {industryName(id)}
                      <button type="button" onClick={() => toggleIndustry(id)} className="font-bold" aria-label={`Remove ${industryName(id)}`}>
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <input
                placeholder="Search industries (e.g. banking, healthcare, retail)"
                value={industrySearch}
                onChange={(e) => setIndustrySearch(e.target.value)}
                className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
              />
              <div className="mt-1 max-h-40 space-y-1 overflow-y-auto rounded-md border border-line p-2">
                {filteredIndustries.map((i) => (
                  <label key={i.industry_id} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={form.industry_ids.includes(i.industry_id)}
                      onChange={() => toggleIndustry(i.industry_id)}
                    />
                    {i.industry_name}
                  </label>
                ))}
                {filteredIndustries.length === 0 && <p className="text-sm text-ink-soft">No industries match "{industrySearch}".</p>}
              </div>
            </div>

            <input
              placeholder="Country"
              value={form.country}
              onChange={(e) => setForm({ ...form, country: e.target.value })}
              className="col-span-2 rounded-md border border-line px-3 py-2 text-sm"
            />
            <input
              required
              placeholder="Primary admin first name"
              value={form.primary_admin_first_name}
              onChange={(e) => setForm({ ...form, primary_admin_first_name: e.target.value })}
              className="rounded-md border border-line px-3 py-2 text-sm"
            />
            <input
              required
              placeholder="Primary admin last name"
              value={form.primary_admin_last_name}
              onChange={(e) => setForm({ ...form, primary_admin_last_name: e.target.value })}
              className="rounded-md border border-line px-3 py-2 text-sm"
            />
            <input
              required
              type="email"
              placeholder="Primary admin email"
              value={form.primary_admin_email}
              onChange={(e) => setForm({ ...form, primary_admin_email: e.target.value })}
              className="col-span-2 rounded-md border border-line px-3 py-2 text-sm"
            />
          </div>
          {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={isSubmitting}
            className="mt-4 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {isSubmitting ? 'Creating…' : 'Create client & send invitation'}
          </button>
        </form>
      )}
    </div>
  )
}
