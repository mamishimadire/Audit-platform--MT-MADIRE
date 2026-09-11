import { useEffect, useState, type FormEvent } from 'react'
import { apiClient, API_BASE_URL } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type { GatewayCreatedOut, GatewayOut } from '../types/api'

const STATUS_STYLES: Record<string, string> = {
  online: 'bg-accent-soft text-accent-ink',
  pending: 'bg-bg text-ink-soft',
  offline: 'bg-orange-50 text-orange-700',
  deregistered: 'bg-red-50 text-red-700',
}

export function GatewaysPage() {
  const { hasRole } = useAuth()
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [gateways, setGateways] = useState<GatewayOut[]>([])
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [justCreated, setJustCreated] = useState<GatewayCreatedOut | null>(null)
  const [copied, setCopied] = useState(false)

  const canManage = hasRole('Platform Super Admin', 'Audit Manager', 'IT/Audit Technical User', 'Client IT Admin')

  const load = (orgId: string) => apiClient.get<GatewayOut[]>(`/organizations/${orgId}/gateways`).then((res) => setGateways(res.data))

  useEffect(() => {
    if (organizationId) load(organizationId)
  }, [organizationId])

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!organizationId) return
    setError(null)
    setIsSubmitting(true)
    try {
      const res = await apiClient.post<GatewayCreatedOut>(`/organizations/${organizationId}/gateways`, { gateway_name: name })
      setJustCreated(res.data)
      setName('')
      load(organizationId)
    } catch {
      setError('Could not create the gateway.')
    } finally {
      setIsSubmitting(false)
    }
  }

  const revoke = async (gatewayId: string) => {
    if (!organizationId) return
    await apiClient.post(`/gateways/${gatewayId}/revoke`)
    load(organizationId)
  }

  const platformUrl = API_BASE_URL
  const copyCommand = async () => {
    try {
      await navigator.clipboard.writeText(platformUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard access denied — the URL is still visible to select manually
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Gateways</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Installed inside the client's own network. Each row here is a real, separately-authenticated process. Windows gets
        a single application file — nothing to unzip.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-4 flex gap-2">
        <a
          href={`${API_BASE_URL}/gateways/download/windows`}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white"
        >
          Download for Windows
        </a>
        <a
          href={`${API_BASE_URL}/gateways/download/linux`}
          className="rounded-md border border-line bg-surface px-4 py-2 text-sm font-medium text-ink"
        >
          Download for Linux
        </a>
      </div>

      {justCreated && (
        <div className="mt-4 rounded-lg border border-accent-soft bg-accent-soft p-4 text-sm">
          <div className="font-semibold text-accent-ink">Registration code — single use, expires in 15 minutes</div>
          <div className="mt-2 font-mono text-lg tracking-wider text-ink">{justCreated.registration_code}</div>
          <p className="mt-2 text-ink-soft">
            On the client machine: save <code>MadireGateway.exe</code> to a permanent folder and double-click it. It opens
            a setup window — paste in the platform URL and this code, click <strong>Register</strong>, then{' '}
            <strong>Edit config.yaml</strong> to add the database connection, then{' '}
            <strong>Install &amp; Start Service</strong>. Windows will ask for administrator permission once.
          </p>
          <div className="mt-2 flex items-center gap-2">
            <span className="text-xs text-ink-soft">Platform URL:</span>
            <code className="flex-1 overflow-x-auto rounded-md bg-surface px-3 py-2 font-mono text-xs text-ink">{platformUrl}</code>
            <button onClick={copyCommand} className="whitespace-nowrap rounded-md bg-accent px-3 py-2 text-xs font-medium text-white">
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
              <th className="px-4 py-2">Registration</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Version</th>
              <th className="px-4 py-2">Last heartbeat</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {gateways.map((g) => (
              <tr key={g.gateway_id} className="border-t border-line">
                <td className="px-4 py-2 font-medium text-ink">{g.gateway_name}</td>
                <td className="px-4 py-2 text-ink-soft">{g.registration_status}</td>
                <td className="px-4 py-2">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[g.status] ?? ''}`}>{g.status}</span>
                </td>
                <td className="px-4 py-2 text-ink-soft">{g.version ?? '—'}</td>
                <td className="px-4 py-2 text-ink-soft">{g.last_heartbeat ? new Date(g.last_heartbeat).toLocaleString() : 'never'}</td>
                <td className="px-4 py-2">
                  {canManage && g.status !== 'deregistered' && (
                    <button onClick={() => revoke(g.gateway_id)} className="text-xs font-medium text-red-600 hover:underline">
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {gateways.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-ink-soft">
                  No gateways yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {canManage && organizationId && (
        <form onSubmit={handleSubmit} className="mt-6 max-w-md rounded-lg border border-line bg-surface p-5">
          <h2 className="text-sm font-semibold text-ink">Register a new gateway</h2>
          <div className="mt-4">
            <input
              required
              placeholder="Gateway name (e.g. Finance DB Gateway)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            />
          </div>
          {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={isSubmitting}
            className="mt-4 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {isSubmitting ? 'Generating…' : 'Generate registration code'}
          </button>
        </form>
      )}
    </div>
  )
}
