import { useEffect, useState, type FormEvent } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import type {
  ConnectionTestResult,
  DataConnectionOut,
  DataEntityOut,
  DataFieldOut,
  DataSourceOut,
  DirectDbType,
  GatewayOut,
  OracleConnectionType,
  SnowflakeAuthMethod,
} from '../types/api'

// SAP HANA's port depends on the instance number (3<NN>13 for the tenant
// DB, 3<NN>15 for legacy single-container systems) — this is only a
// starting-point suggestion, not a real default; the field stays editable.
// Snowflake has no port field at all (its dialect resolves one internally) —
// this entry is never shown or used, just present so the Record stays total.
const DEFAULT_PORT: Record<DirectDbType, number> = { postgresql: 5432, mysql: 3306, mssql: 1433, oracle: 1521, sap_hana: 30013, snowflake: 443 }
const DB_TYPE_LABELS: Record<DirectDbType, string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL',
  mssql: 'Microsoft SQL Server',
  oracle: 'Oracle Database',
  sap_hana: 'SAP HANA',
  snowflake: 'Snowflake',
}

type Provider = 'neon' | 'supabase' | 'aws_rds' | 'azure_sql' | 'gcp_sql' | 'other'

const PROVIDER_LABELS: Record<Provider, string> = {
  neon: 'Neon',
  supabase: 'Supabase',
  aws_rds: 'AWS RDS',
  azure_sql: 'Azure SQL Database',
  gcp_sql: 'Google Cloud SQL',
  other: 'Other / self-hosted',
}

// Only Neon/Supabase pin a single engine — RDS/Cloud SQL support several,
// so those leave db_type for the user to pick explicitly instead of guessing.
const PROVIDER_DB_TYPE: Partial<Record<Provider, DirectDbType>> = { neon: 'postgresql', supabase: 'postgresql' }

const PROVIDER_GUIDES: Record<Provider, string[]> = {
  neon: [
    "Open your Neon project and click Connect (top left of the dashboard).",
    'Under "Postgres database", note the Host, Database and Role fields, and click "Show password" to reveal it.',
    'Either connection works here — pooled ("...-pooler...") or direct. Copy the host, port (5432), database, role (username) and password below.',
  ],
  supabase: [
    'Open your Supabase project and click Connect (top right), then the "Postgres database" tab.',
    'Under "Connection parameters", copy the host, port, database (usually "postgres") and user.',
    "If you don't know the password, reset it from Project Settings → Database → Reset database password.",
  ],
  aws_rds: [
    'AWS Console → RDS → Databases → select your instance.',
    'Under "Connectivity & security", copy the Endpoint (host) and Port.',
    'Database name and master username are whatever was set when the instance was created (check the "Configuration" tab if unsure); reset the password via "Modify" if forgotten.',
    "Under the instance's security group, add an inbound rule allowing this platform to reach that port — a private RDS instance with no public access can't be reached this way at all.",
  ],
  azure_sql: [
    'Azure Portal → SQL databases → select your database → Overview.',
    'Copy the Server name (host — ends in .database.windows.net) and use port 1433.',
    "Username/password are the SQL authentication credentials set when the server was created.",
    'Under "Networking", add a firewall rule allowing this platform\'s outbound address, or "Allow Azure services" if that\'s acceptable for your setup.',
  ],
  gcp_sql: [
    'Google Cloud Console → SQL → select your instance → Overview.',
    'Copy the Public IP address (host) — port 5432 for PostgreSQL or 3306 for MySQL.',
    'Database name is under the "Databases" tab; username/password under "Users" (reset one there if needed).',
    'Under "Connections", add this platform\'s outbound address to Authorized networks.',
  ],
  other: [
    'Ask whoever manages this database for: the hostname or IP address, port, database name, and a username/password with read access.',
    "If it sits behind a firewall or VPC, that firewall needs an inbound rule allowing this platform to reach it — an address unreachable from the internet can't be connected to this way.",
  ],
}

function EntityRow({ entity }: { entity: DataEntityOut }) {
  const [open, setOpen] = useState(false)
  const [fields, setFields] = useState<DataFieldOut[] | null>(null)

  const toggle = () => {
    if (!open && fields === null) {
      apiClient.get<DataFieldOut[]>(`/entities/${entity.entity_id}/fields`).then((res) => setFields(res.data))
    }
    setOpen(!open)
  }

  return (
    <div className="border-t border-line first:border-t-0">
      <button onClick={toggle} className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-bg">
        <span className="font-mono">{entity.entity_name}</span>
        <span className="text-xs text-ink-soft">{entity.entity_type}</span>
      </button>
      {open && fields && (
        <div className="bg-bg px-3 py-2">
          {fields.map((f) => (
            <div key={f.field_id} className="flex items-center gap-2 py-0.5 font-mono text-xs">
              {f.is_primary_key && <span className="rounded bg-accent-soft px-1 text-accent-ink">PK</span>}
              <span>{f.field_name}</span>
              <span className="text-ink-soft">{f.data_type}</span>
            </div>
          ))}
          {fields.length === 0 && <div className="py-1 text-xs text-ink-soft">No fields discovered.</div>}
        </div>
      )}
    </div>
  )
}

function ConnectionRow({ connection, canManage, onDiscovered }: { connection: DataConnectionOut; canManage: boolean; onDiscovered: () => void }) {
  const [testing, setTesting] = useState(false)
  const [discovering, setDiscovering] = useState(false)
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const runTest = async () => {
    setTesting(true)
    setActionError(null)
    setTestResult(null)
    try {
      const res = await apiClient.post<ConnectionTestResult>(`/connections/${connection.connection_id}/test`)
      setTestResult(res.data)
    } catch (err: any) {
      setActionError(err?.response?.data?.detail ?? 'Could not test this connection.')
    } finally {
      setTesting(false)
    }
  }

  const runDiscover = async () => {
    setDiscovering(true)
    setActionError(null)
    try {
      await apiClient.post(`/connections/${connection.connection_id}/discover`)
      onDiscovered()
    } catch (err: any) {
      setActionError(err?.response?.data?.detail ?? 'Could not read the schema from this database.')
    } finally {
      setDiscovering(false)
    }
  }

  return (
    <div className="mt-2 rounded-md border border-line px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm">
          {connection.connection_mode === 'direct' ? (
            <span className="font-mono text-xs text-ink">
              {connection.db_type === 'snowflake' ? (
                <>
                  snowflake · {connection.host}/{connection.database_name}/{connection.snowflake_schema}
                  {connection.snowflake_warehouse && ` (warehouse: ${connection.snowflake_warehouse})`}
                </>
              ) : (
                <>
                  {connection.db_type} · {connection.host}:{connection.port}/{connection.database_name}
                </>
              )}
              <span className="text-ink-soft"> (user: {connection.username})</span>
            </span>
          ) : (
            <span className="font-mono text-xs text-ink-soft">Gateway connection · {connection.connection_id.slice(0, 8)}</span>
          )}
        </div>
        <span className="text-xs text-ink-soft">
          {connection.connection_status}
          {connection.last_tested_at ? ` · tested ${new Date(connection.last_tested_at).toLocaleString()}` : ''}
        </span>
      </div>
      {connection.connection_mode === 'direct' && connection.db_type === 'snowflake' && (
        <p className="mt-2 text-xs text-amber-700">
          Snowflake cost notice: testing or discovering schema here may consume Snowflake compute credits.
        </p>
      )}
      {connection.connection_mode === 'direct' && canManage && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            onClick={runTest}
            disabled={testing}
            className="rounded-md border border-line px-2 py-1 text-xs font-medium text-ink hover:bg-bg disabled:opacity-60"
          >
            {testing ? 'Testing…' : 'Test connection'}
          </button>
          <button
            onClick={runDiscover}
            disabled={discovering}
            className="rounded-md border border-line px-2 py-1 text-xs font-medium text-ink hover:bg-bg disabled:opacity-60"
          >
            {discovering ? 'Reading schema…' : 'Discover schema'}
          </button>
          {testResult && (
            <span className={`text-xs ${testResult.success ? 'text-accent-ink' : 'text-red-600'}`}>
              {testResult.success ? '✓' : '✗'} {testResult.detail}
            </span>
          )}
          {actionError && <span className="text-xs text-red-600">{actionError}</span>}
        </div>
      )}
    </div>
  )
}

interface HubSpotStatus {
  oauth_connection_id: string
  connection_id: string
  authorization_status: 'pending' | 'authorized' | 'expired' | 'revoked'
  expires_at: string | null
  scope: string | null
  needs_reauthorization: boolean
}

function HubSpotConnectionPanel({ source, canManage }: { source: DataSourceOut; canManage: boolean }) {
  const [status, setStatus] = useState<HubSpotStatus | 'none' | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [discovering, setDiscovering] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadStatus = () =>
    apiClient
      .get<HubSpotStatus>(`/organizations/${source.organization_id}/data-sources/${source.data_source_id}/hubspot/status`)
      .then((res) => setStatus(res.data))
      .catch(() => setStatus('none'))

  useEffect(() => {
    loadStatus()
    // Picks up the ?hubspot=connected/failed redirect back from HubSpot's
    // own consent screen without the user needing to manually refresh.
    const params = new URLSearchParams(window.location.search)
    if (params.get('hubspot')) {
      window.history.replaceState({}, '', window.location.pathname)
      loadStatus()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.data_source_id])

  const connect = async () => {
    setConnecting(true)
    setError(null)
    try {
      const res = await apiClient.post<{ authorize_url: string }>(
        `/organizations/${source.organization_id}/data-sources/${source.data_source_id}/hubspot/connect`,
      )
      window.location.href = res.data.authorize_url
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not start the HubSpot connection.')
      setConnecting(false)
    }
  }

  const discover = async () => {
    setDiscovering(true)
    setError(null)
    try {
      const res = await apiClient.post<HubSpotStatus>(
        `/organizations/${source.organization_id}/data-sources/${source.data_source_id}/hubspot/discover`,
      )
      setStatus(res.data)
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not read the schema from HubSpot.')
    } finally {
      setDiscovering(false)
    }
  }

  if (status === null) return null

  const hasConnection = status !== 'none'
  const needsReauth = hasConnection && status.needs_reauthorization

  return (
    <div className="border-t border-line px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">HubSpot connection</div>
      {needsReauth && (
        <div className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm font-medium text-red-700">
          Connection lost — reauthorization required. HubSpot access was revoked; audit checks against this data
          source are paused, not silently skipped.
        </div>
      )}
      {!hasConnection && (
        <p className="mt-1 text-xs text-ink-soft">
          Not connected yet. Connecting redirects to HubSpot, where an authorized user on the client's HubSpot
          account grants MT AUDIT read-only access — no password is ever entered here.
        </p>
      )}
      {hasConnection && !needsReauth && (
        <p className="mt-1 text-xs text-ink-soft">
          Status: <span className="font-medium text-accent-ink">{status.authorization_status}</span>
          {status.expires_at && ` · token refreshes automatically before ${new Date(status.expires_at).toLocaleString()}`}
        </p>
      )}
      {canManage && (
        <div className="mt-2 flex flex-wrap gap-2">
          {(!hasConnection || needsReauth) && (
            <button
              onClick={connect}
              disabled={connecting}
              className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
            >
              {connecting ? 'Redirecting…' : needsReauth ? 'Reauthorize with HubSpot' : 'Connect with HubSpot'}
            </button>
          )}
          {hasConnection && !needsReauth && (
            <button
              onClick={discover}
              disabled={discovering}
              className="rounded-md border border-line px-2 py-1 text-xs font-medium text-ink hover:bg-bg disabled:opacity-60"
            >
              {discovering ? 'Reading schema…' : 'Discover schema (contacts, companies, deals)'}
            </button>
          )}
        </div>
      )}
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  )
}

function DataSourceCard({ source, gateways, canManage }: { source: DataSourceOut; gateways: GatewayOut[]; canManage: boolean }) {
  const [connections, setConnections] = useState<DataConnectionOut[]>([])
  const [entities, setEntities] = useState<DataEntityOut[]>([])
  const [mode, setMode] = useState<'gateway' | 'direct'>('gateway')
  const [selectedGatewayId, setSelectedGatewayId] = useState('')
  const [direct, setDirect] = useState({
    db_type: 'postgresql' as DirectDbType,
    host: '',
    port: DEFAULT_PORT.postgresql,
    database_name: '',
    username: '',
    password: '',
    oracle_connection_type: 'service_name' as OracleConnectionType,
    sap_hana_encrypt: true,
    snowflake_warehouse: '',
    snowflake_schema: '',
    snowflake_role: '',
    snowflake_auth_method: 'password' as SnowflakeAuthMethod,
    snowflake_key_passphrase: '',
  })
  const [isAddingConnection, setIsAddingConnection] = useState(false)
  const [connectionError, setConnectionError] = useState<string | null>(null)

  const load = () => {
    apiClient.get<DataConnectionOut[]>(`/data-sources/${source.data_source_id}/connections`).then((res) => setConnections(res.data))
    apiClient.get<DataEntityOut[]>(`/data-sources/${source.data_source_id}/entities`).then((res) => setEntities(res.data))
  }

  useEffect(load, [source.data_source_id])

  const createConnection = async () => {
    if (isAddingConnection) return // already in flight — ignore extra clicks instead of firing again
    setIsAddingConnection(true)
    setConnectionError(null)
    try {
      if (mode === 'gateway') {
        await apiClient.post(`/data-sources/${source.data_source_id}/connections`, { gateway_id: selectedGatewayId || null })
      } else {
        await apiClient.post(`/data-sources/${source.data_source_id}/connections/direct`, direct)
        setDirect({
          db_type: 'postgresql',
          host: '',
          port: DEFAULT_PORT.postgresql,
          database_name: '',
          username: '',
          password: '',
          oracle_connection_type: 'service_name',
          sap_hana_encrypt: true,
          snowflake_warehouse: '',
          snowflake_schema: '',
          snowflake_role: '',
          snowflake_auth_method: 'password',
          snowflake_key_passphrase: '',
        })
      }
      load()
    } catch (err: any) {
      setConnectionError(err?.response?.data?.detail ?? 'Could not add this connection — it may already exist, or the request failed.')
    } finally {
      setIsAddingConnection(false)
    }
  }

  // SAP HANA needs no database/tenant name for a normal tenant-DB connection.
  // Snowflake needs no port, but does need a warehouse + schema instead.
  const directFormValid =
    direct.host &&
    direct.username &&
    direct.password &&
    (direct.db_type === 'snowflake' || direct.port) &&
    (direct.db_type === 'sap_hana' || direct.database_name) &&
    (direct.db_type !== 'snowflake' || (direct.snowflake_warehouse && direct.snowflake_schema))

  return (
    <div className="rounded-lg border border-line bg-surface">
      <div className="flex items-center justify-between px-4 py-3">
        <div>
          <div className="font-medium text-ink">{source.source_name}</div>
          <div className="text-xs text-ink-soft">
            {source.source_type} · {source.environment}
          </div>
        </div>
        <span className="rounded-full bg-bg px-2 py-0.5 text-xs text-ink-soft">{source.status}</span>
      </div>

      {source.source_type === 'hubspot' ? (
        <HubSpotConnectionPanel source={source} canManage={canManage} />
      ) : (
      <div className="border-t border-line px-4 py-3">
        <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Connections</div>
        {connections.map((c) => (
          <ConnectionRow key={c.connection_id} connection={c} canManage={canManage} onDiscovered={load} />
        ))}
        {connections.length === 0 && <div className="mt-1 text-sm text-ink-soft">No connection yet.</div>}

        {canManage && (
          <div className="mt-3 rounded-md border border-line p-3">
            <div className="flex gap-2 text-xs font-medium">
              <button
                onClick={() => setMode('gateway')}
                className={`rounded-md px-2 py-1 ${mode === 'gateway' ? 'bg-accent text-white' : 'bg-bg text-ink-soft'}`}
              >
                Via Gateway
              </button>
              <button
                onClick={() => setMode('direct')}
                className={`rounded-md px-2 py-1 ${mode === 'direct' ? 'bg-accent text-white' : 'bg-bg text-ink-soft'}`}
              >
                Connect directly to a cloud database
              </button>
            </div>

            {mode === 'gateway' ? (
              <div className="mt-2 flex gap-2">
                <select
                  value={selectedGatewayId}
                  onChange={(e) => setSelectedGatewayId(e.target.value)}
                  className="flex-1 rounded-md border border-line px-2 py-1 text-sm"
                >
                  <option value="">Select a gateway…</option>
                  {gateways.map((g) => (
                    <option key={g.gateway_id} value={g.gateway_id}>
                      {g.gateway_name}
                    </option>
                  ))}
                </select>
                <button
                  onClick={createConnection}
                  disabled={isAddingConnection || !selectedGatewayId}
                  className="whitespace-nowrap rounded-md bg-accent px-3 py-1 text-xs font-medium text-white disabled:opacity-60"
                >
                  {isAddingConnection ? 'Adding…' : 'Add connection'}
                </button>
              </div>
            ) : (
              <div className="mt-2 space-y-2">
                <p className="text-xs text-ink-soft">
                  For a cloud-hosted database reachable from the internet (no on-premise Gateway needed) — the
                  platform connects to it directly. Credentials are encrypted before storage and are never shown again.
                </p>
                {direct.db_type === 'snowflake' && (
                  <p className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-xs text-amber-800">
                    Snowflake cost notice: connection tests, schema discovery and scheduled audit queries may consume
                    Snowflake compute credits. Configure execution frequency appropriately.
                  </p>
                )}
                <div className="flex gap-2">
                  <select
                    value={direct.db_type}
                    onChange={(e) => {
                      const db_type = e.target.value as DirectDbType
                      setDirect({ ...direct, db_type, port: DEFAULT_PORT[db_type] })
                    }}
                    className="rounded-md border border-line px-2 py-1 text-sm"
                  >
                    {(Object.keys(DB_TYPE_LABELS) as DirectDbType[]).map((t) => (
                      <option key={t} value={t}>
                        {DB_TYPE_LABELS[t]}
                      </option>
                    ))}
                  </select>
                  <input
                    placeholder={direct.db_type === 'snowflake' ? 'Account identifier (e.g. xy12345.us-east-1)' : 'Host (e.g. db.example.com)'}
                    value={direct.host}
                    onChange={(e) => setDirect({ ...direct, host: e.target.value })}
                    className="flex-1 rounded-md border border-line px-2 py-1 text-sm"
                  />
                  {direct.db_type !== 'snowflake' && (
                    <input
                      type="number"
                      placeholder="Port"
                      value={direct.port}
                      onChange={(e) => setDirect({ ...direct, port: Number(e.target.value) })}
                      className="w-20 rounded-md border border-line px-2 py-1 text-sm"
                    />
                  )}
                </div>
                {direct.db_type === 'oracle' && (
                  <div className="flex gap-2 text-xs font-medium">
                    <button
                      type="button"
                      onClick={() => setDirect({ ...direct, oracle_connection_type: 'service_name' })}
                      className={`rounded-md px-2 py-1 ${direct.oracle_connection_type === 'service_name' ? 'bg-accent text-white' : 'bg-bg text-ink-soft'}`}
                    >
                      Service Name
                    </button>
                    <button
                      type="button"
                      onClick={() => setDirect({ ...direct, oracle_connection_type: 'sid' })}
                      className={`rounded-md px-2 py-1 ${direct.oracle_connection_type === 'sid' ? 'bg-accent text-white' : 'bg-bg text-ink-soft'}`}
                    >
                      SID
                    </button>
                  </div>
                )}
                {direct.db_type !== 'sap_hana' && (
                  <input
                    placeholder={
                      direct.db_type === 'oracle'
                        ? direct.oracle_connection_type === 'sid'
                          ? 'SID (e.g. ORCL)'
                          : 'Service Name (e.g. ORCLPDB1)'
                        : direct.db_type === 'snowflake'
                          ? 'Database'
                          : 'Database name'
                    }
                    value={direct.database_name}
                    onChange={(e) => setDirect({ ...direct, database_name: e.target.value })}
                    className="w-full rounded-md border border-line px-2 py-1 text-sm"
                  />
                )}
                {direct.db_type === 'sap_hana' && (
                  <label className="flex items-center gap-2 text-xs text-ink">
                    <input
                      type="checkbox"
                      checked={direct.sap_hana_encrypt}
                      onChange={(e) => setDirect({ ...direct, sap_hana_encrypt: e.target.checked })}
                    />
                    Require encryption / TLS (recommended — only turn off if this instance genuinely doesn't support it)
                  </label>
                )}
                {direct.db_type === 'snowflake' && (
                  <>
                    <div className="flex gap-2">
                      <input
                        placeholder="Schema (e.g. PUBLIC)"
                        value={direct.snowflake_schema}
                        onChange={(e) => setDirect({ ...direct, snowflake_schema: e.target.value })}
                        className="flex-1 rounded-md border border-line px-2 py-1 text-sm"
                      />
                      <input
                        placeholder="Warehouse (e.g. AUDIT_WH)"
                        value={direct.snowflake_warehouse}
                        onChange={(e) => setDirect({ ...direct, snowflake_warehouse: e.target.value })}
                        className="flex-1 rounded-md border border-line px-2 py-1 text-sm"
                      />
                      <input
                        placeholder="Role (optional)"
                        value={direct.snowflake_role}
                        onChange={(e) => setDirect({ ...direct, snowflake_role: e.target.value })}
                        className="flex-1 rounded-md border border-line px-2 py-1 text-sm"
                      />
                    </div>
                    <div className="flex gap-2 text-xs font-medium">
                      <button
                        type="button"
                        onClick={() => setDirect({ ...direct, snowflake_auth_method: 'password' })}
                        className={`rounded-md px-2 py-1 ${direct.snowflake_auth_method === 'password' ? 'bg-accent text-white' : 'bg-bg text-ink-soft'}`}
                      >
                        Password
                      </button>
                      <button
                        type="button"
                        onClick={() => setDirect({ ...direct, snowflake_auth_method: 'key_pair' })}
                        className={`rounded-md px-2 py-1 ${direct.snowflake_auth_method === 'key_pair' ? 'bg-accent text-white' : 'bg-bg text-ink-soft'}`}
                      >
                        Key pair
                      </button>
                    </div>
                  </>
                )}
                <div className="flex gap-2">
                  <input
                    placeholder="Username"
                    value={direct.username}
                    onChange={(e) => setDirect({ ...direct, username: e.target.value })}
                    className="flex-1 rounded-md border border-line px-2 py-1 text-sm"
                  />
                  {direct.db_type === 'snowflake' && direct.snowflake_auth_method === 'key_pair' ? (
                    <textarea
                      placeholder="Private key (PEM) — paste the full -----BEGIN PRIVATE KEY----- block"
                      value={direct.password}
                      onChange={(e) => setDirect({ ...direct, password: e.target.value })}
                      rows={3}
                      className="flex-1 rounded-md border border-line px-2 py-1 font-mono text-xs"
                    />
                  ) : (
                    <input
                      type="password"
                      placeholder="Password"
                      value={direct.password}
                      onChange={(e) => setDirect({ ...direct, password: e.target.value })}
                      className="flex-1 rounded-md border border-line px-2 py-1 text-sm"
                    />
                  )}
                </div>
                {direct.db_type === 'snowflake' && direct.snowflake_auth_method === 'key_pair' && (
                  <input
                    type="password"
                    placeholder="Private key passphrase (optional — only if the key itself is encrypted)"
                    value={direct.snowflake_key_passphrase}
                    onChange={(e) => setDirect({ ...direct, snowflake_key_passphrase: e.target.value })}
                    className="w-full rounded-md border border-line px-2 py-1 text-sm"
                  />
                )}
                <button
                  onClick={createConnection}
                  disabled={isAddingConnection || !directFormValid}
                  className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
                >
                  {isAddingConnection ? 'Adding…' : 'Add connection'}
                </button>
              </div>
            )}
          </div>
        )}
        {connectionError && <p className="mt-1 text-xs text-red-600">{connectionError}</p>}
      </div>
      )}

      {entities.length > 0 && (
        <div className="border-t border-line">
          <div className="px-4 pt-3 text-xs font-medium uppercase tracking-wide text-ink-soft">
            Discovered tables ({entities.length})
          </div>
          {entities.map((e) => (
            <EntityRow key={e.entity_id} entity={e} />
          ))}
        </div>
      )}
    </div>
  )
}

export function DataSourcesPage() {
  const { hasRole } = useAuth()
  const { organizationId, setOrganizationId, organizations, needsPicker } = useActiveOrganization()
  const [sources, setSources] = useState<DataSourceOut[]>([])
  const [gateways, setGateways] = useState<GatewayOut[]>([])
  const [form, setForm] = useState({ source_name: '', source_type: 'postgresql', environment: 'on_premise' })
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const canManage = hasRole('Platform Super Admin', 'Audit Manager', 'IT/Audit Technical User', 'Client IT Admin')

  const load = (orgId: string) => {
    apiClient.get<DataSourceOut[]>(`/organizations/${orgId}/data-sources`).then((res) => setSources(res.data))
    apiClient.get<GatewayOut[]>(`/organizations/${orgId}/gateways`).then((res) => setGateways(res.data))
  }

  useEffect(() => {
    if (organizationId) load(organizationId)
  }, [organizationId])

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!organizationId) return
    setError(null)
    setIsSubmitting(true)
    try {
      await apiClient.post(`/organizations/${organizationId}/data-sources`, form)
      setForm({ source_name: '', source_type: 'postgresql', environment: 'on_premise' })
      load(organizationId)
    } catch {
      setError('Could not create the data source.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Data Sources</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Register a source here, then point a Gateway's local config at the resulting connection ID — the Gateway
        reports test results and discovery back on its own schedule.
      </p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      <div className="mt-4 space-y-4">
        {sources.map((s) => (
          <DataSourceCard key={s.data_source_id} source={s} gateways={gateways} canManage={canManage} />
        ))}
        {sources.length === 0 && (
          <div className="rounded-lg border border-line bg-surface px-4 py-6 text-center text-sm text-ink-soft">
            No data sources yet.
          </div>
        )}
      </div>

      {canManage && organizationId && (
        <form onSubmit={handleSubmit} className="mt-6 max-w-md rounded-lg border border-line bg-surface p-5">
          <h2 className="text-sm font-semibold text-ink">Register a data source</h2>
          <div className="mt-4 space-y-3">
            <input
              required
              placeholder="Name (e.g. Finance DB)"
              value={form.source_name}
              onChange={(e) => setForm({ ...form, source_name: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            />
            <select
              value={form.source_type}
              onChange={(e) => setForm({ ...form, source_type: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            >
              <option value="postgresql">PostgreSQL</option>
              <option value="sql_server">Microsoft SQL Server</option>
              <option value="mysql">MySQL</option>
              <option value="hubspot">HubSpot (CRM)</option>
            </select>
            <select
              value={form.environment}
              onChange={(e) => setForm({ ...form, environment: e.target.value })}
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
            >
              <option value="on_premise">On-premise / private network (needs a Gateway)</option>
              <option value="cloud">Cloud-hosted (direct connection)</option>
            </select>
          </div>
          {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={isSubmitting}
            className="mt-4 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {isSubmitting ? 'Creating…' : 'Register data source'}
          </button>
        </form>
      )}
    </div>
  )
}
