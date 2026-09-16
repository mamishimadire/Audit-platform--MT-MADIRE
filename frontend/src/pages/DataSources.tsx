import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type {
  ConnectionTestResult,
  DataConnectionChangeOut,
  DataConnectionOut,
  DataEntityOut,
  DataFieldOut,
  DataSourceOut,
  DirectDbType,
  EligibleApproverOut,
  GatewayOut,
  OracleConnectionType,
  SnowflakeAuthMethod,
} from '../types/api'

// SAP HANA's port depends on the instance number (3<NN>13 for the tenant
// DB, 3<NN>15 for legacy single-container systems) — this is only a
// starting-point suggestion, not a real default; the field stays editable.
// Snowflake has no port field at all (its dialect resolves one internally) —
// this entry is never shown or used, just present so the Record stays total.
// MongoDB's default (27017) only applies outside SRV mode — an Atlas/SRV
// connection resolves its real hosts+ports via DNS, so the port field is
// hidden entirely for the (default) SRV case; see direct.mongodb_srv below.
const DEFAULT_PORT: Record<DirectDbType, number> = { postgresql: 5432, mysql: 3306, mssql: 1433, oracle: 1521, sap_hana: 30013, snowflake: 443, mongodb: 27017 }
const DB_TYPE_LABELS: Record<DirectDbType, string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL',
  mssql: 'Microsoft SQL Server',
  oracle: 'Oracle Database',
  sap_hana: 'SAP HANA',
  snowflake: 'Snowflake',
  mongodb: 'MongoDB',
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

// Which cloud-hosting guides above are actually relevant to each Postgres/
// MySQL/MSSQL engine — Azure SQL is SQL-Server-specific, Neon/Supabase are
// Postgres-only; "Other" always applies. Oracle/SAP HANA/Snowflake/MongoDB
// get their own guide below instead (DB_TYPE_GUIDES) since for those the
// engine itself, not "which cloud host", is what a user needs steps for.
const PROVIDERS_FOR_DB_TYPE: Partial<Record<DirectDbType, Provider[]>> = {
  postgresql: ['neon', 'supabase', 'aws_rds', 'gcp_sql', 'other'],
  mysql: ['aws_rds', 'gcp_sql', 'other'],
  mssql: ['azure_sql', 'aws_rds', 'other'],
}

// Step-by-step "where do I find these values" guidance for the four engines
// that aren't just "Postgres/MySQL/MSSQL hosted somewhere" — each of these
// is its own product with its own console.
const DB_TYPE_GUIDES: Partial<Record<DirectDbType, string[]>> = {
  oracle: [
    'Oracle Cloud (Autonomous Database): Oracle Cloud Console → your ADB instance → DB Connection. Copy the host and port from the connection string shown there — the Service Name is usually listed alongside it (use "Service Name" below, not SID, unless you specifically know this instance uses SID addressing).',
    'Self-hosted / on-prem Oracle: ask your DBA for the host, listener port (usually 1521), and Service Name or SID — and confirm the account has read-only access, since an audit connection should never be able to write.',
    "If this instance isn't reachable from the internet, switch to \"Via Gateway\" above instead — install a Gateway inside that network from the Gateways page.",
  ],
  sap_hana: [
    'SAP BTP / HANA Cloud: BTP Cockpit → your HANA Cloud instance → Connections tab. Copy the Host and Port shown there (the port depends on the instance number, per the hint on that field below).',
    'Self-hosted SAP HANA: ask your Basis/DBA team for the tenant database host and SQL port, and whether TLS is enforced on that instance (affects the "Require encryption" checkbox below).',
    "If this instance isn't reachable from the internet, switch to \"Via Gateway\" above instead.",
  ],
  snowflake: [
    'Snowsight (Snowflake\'s web UI) → bottom-left account name → "Connect a tool to Snowflake", or Admin → Accounts — this shows your account identifier (e.g. xy12345.us-east-1). That identifier is what goes in "Host" below, not a real hostname.',
    'Warehouse, database and schema are listed in the left sidebar of Snowsight. Ask your Snowflake admin for a role with read-only access to the schema this audit needs.',
    'Prefer key-pair auth if your account disallows passwords: generate an RSA key pair, register the public key on your Snowflake user (ALTER USER ... SET RSA_PUBLIC_KEY=...), then paste the private key below.',
  ],
  mongodb: [
    'MongoDB Atlas: Database → your cluster → Connect → Drivers → choose Python → copy the connection string shown there. The part between "@" and the next "/" is the cluster address that goes in "Host" below.',
    'Atlas will refuse every connection (including this one) until this platform\'s address is added to its Network Access allowlist — this platform doesn\'t have one fixed address yet, so use "Allow from anywhere" for now (see the exact steps below). This is a one-time setup step, not something you\'ll be asked to do again.',
    'In the left sidebar of cloud.mongodb.com, click "Network Access" (under Security).',
    'Click the "+ ADD IP ADDRESS" button.',
    'If you see an "ALLOW ACCESS FROM ANYWHERE" button, click it — it fills in 0.0.0.0/0 automatically. If instead you see a plain text box ("Access List Entry"), type 0.0.0.0/0 into it yourself.',
    '(Optional) In "Comment", type something like "MT AUDIT platform access" so it\'s clear later why this entry exists.',
    'Important: if there\'s a toggle for "This entry is temporary and will be deleted in [x] hours", leave it OFF. If it\'s left on, this entry deletes itself and the connection will start failing again after a few hours.',
    'Click "Confirm". It shows as "Pending" for a minute or two, then "Active" — that\'s permanent from then on, regardless of what server this platform runs on.',
    'We\'ll switch this to a specific, tighter IP allowlist once we\'re on a paid plan with a fixed outbound address — you won\'t need to do anything differently when that happens; we\'ll simply add the new address alongside this one.',
    'Self-hosted MongoDB / replica set (not Atlas): uncheck "Use SRV connection" below and provide the host and port directly.',
  ],
}

function EntityRow({ entity, onToggleHidden }: { entity: DataEntityOut; onToggleHidden: (entity: DataEntityOut) => void }) {
  const [open, setOpen] = useState(false)
  const [fields, setFields] = useState<DataFieldOut[] | null>(null)

  const toggle = () => {
    if (!open && fields === null) {
      apiClient.get<DataFieldOut[]>(`/entities/${entity.entity_id}/fields`).then((res) => setFields(res.data))
    }
    setOpen(!open)
  }

  return (
    <div className={`border-t border-line first:border-t-0 ${entity.is_hidden ? 'opacity-50' : ''}`}>
      <div className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-bg">
        <button onClick={toggle} className="flex flex-1 items-center gap-2 text-left">
          <span className="font-mono">{entity.entity_name}</span>
          <span className="text-xs text-ink-soft">{entity.entity_type}</span>
          {entity.is_hidden && <span className="text-[11px] text-ink-faint">(hidden)</span>}
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation()
            onToggleHidden(entity)
          }}
          className="ml-2 text-xs font-medium text-ink-soft hover:text-ink hover:underline"
        >
          {entity.is_hidden ? 'Unhide' : 'Hide'}
        </button>
      </div>
      {open && fields && (
        <div className="bg-bg px-3 py-2">
          {entity.entity_type === 'collection' && entity.description && (
            <p className="mb-1.5 text-[11px] italic text-ink-soft">
              Inferred schema: {entity.description}
            </p>
          )}
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

const CONNECTION_CHANGE_FIELD_LABELS: Record<string, string> = {
  connection_name: 'name',
  host: 'host',
  port: 'port',
  database_name: 'database',
  username: 'username',
  encrypted_password: 'password',
  encrypted_snowflake_key_passphrase: 'Snowflake key passphrase',
  oracle_connection_type: 'Oracle connection type',
  sap_hana_encrypt: 'SAP HANA encryption',
  snowflake_warehouse: 'Snowflake warehouse',
  snowflake_schema: 'Snowflake schema',
  snowflake_role: 'Snowflake role',
  snowflake_auth_method: 'Snowflake auth method',
  mongodb_srv: 'MongoDB SRV mode',
}

function describeConnectionChange(change: DataConnectionChangeOut): string {
  if (change.change_type === 'disconnect') return 'Disconnect this connection'
  if (change.change_type === 'delete') return 'Permanently delete this connection'
  const parts = Object.keys(change.proposed_changes).map((k) => CONNECTION_CHANGE_FIELD_LABELS[k] ?? k)
  return `Update ${parts.join(', ')}`
}

function ConnectionRow({
  connection,
  canManage,
  canApproveChange,
  eligibleApprovers,
  onDiscovered,
  onChanged,
  onToggleHidden,
}: {
  connection: DataConnectionOut
  canManage: boolean
  canApproveChange: boolean
  eligibleApprovers: EligibleApproverOut[]
  onDiscovered: () => void
  onChanged: () => void
  onToggleHidden: (connection: DataConnectionOut) => void
}) {
  const { user } = useAuth()
  const [testing, setTesting] = useState(false)
  const [discovering, setDiscovering] = useState(false)
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [changes, setChanges] = useState<DataConnectionChangeOut[]>([])
  const [editing, setEditing] = useState(false)
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [busyChangeId, setBusyChangeId] = useState<string | null>(null)
  const [rejectingChangeId, setRejectingChangeId] = useState<string | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  const [editForm, setEditForm] = useState({
    connection_name: connection.connection_name ?? '',
    host: connection.host ?? '',
    port: connection.port ?? 0,
    database_name: connection.database_name ?? '',
    username: connection.username ?? '',
    password: '',
  })

  const loadChanges = () =>
    apiClient.get<DataConnectionChangeOut[]>(`/connections/${connection.connection_id}/changes`).then((res) => setChanges(res.data))

  useEffect(() => {
    loadChanges()
  }, [connection.connection_id])

  const pending = changes.find((c) => c.approval_status === 'pending_approval') ?? null
  const recentDecided = changes.filter((c) => c.approval_status !== 'pending_approval').slice(0, 3)
  const approverNames = eligibleApprovers.length === 0 ? 'an authorized approver' : eligibleApprovers.map((a) => `${a.first_name} ${a.last_name}`).join(', ')
  const showsPort = connection.db_type !== 'snowflake' && !(connection.db_type === 'mongodb' && connection.mongodb_srv)
  const revoked = connection.connection_status === 'revoked'

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

  const buildUpdatePayload = (): Record<string, unknown> => {
    const payload: Record<string, unknown> = {}
    if (editForm.connection_name !== (connection.connection_name ?? '')) payload.connection_name = editForm.connection_name
    if (editForm.host !== (connection.host ?? '')) payload.host = editForm.host
    if (showsPort && editForm.port !== connection.port) payload.port = editForm.port
    if (editForm.database_name !== (connection.database_name ?? '')) payload.database_name = editForm.database_name
    if (editForm.username !== (connection.username ?? '')) payload.username = editForm.username
    if (editForm.password) payload.password = editForm.password
    return payload
  }
  const editPayload = buildUpdatePayload()

  const submitEdit = async () => {
    if (Object.keys(editPayload).length === 0) {
      setEditing(false)
      return
    }
    setSubmitting(true)
    setActionError(null)
    try {
      await apiClient.post(`/connections/${connection.connection_id}/changes/update`, editPayload)
      setEditing(false)
      setEditForm((f) => ({ ...f, password: '' }))
      await loadChanges()
      onChanged()
    } catch (err: any) {
      setActionError(err?.response?.data?.detail ?? 'Could not submit this change for approval.')
    } finally {
      setSubmitting(false)
    }
  }

  const requestDisconnect = async () => {
    setSubmitting(true)
    setActionError(null)
    try {
      await apiClient.post(`/connections/${connection.connection_id}/changes/disconnect`)
      setConfirmingDisconnect(false)
      await loadChanges()
      onChanged()
    } catch (err: any) {
      setActionError(err?.response?.data?.detail ?? 'Could not submit the disconnect request.')
    } finally {
      setSubmitting(false)
    }
  }

  const requestDelete = async () => {
    setSubmitting(true)
    setActionError(null)
    try {
      await apiClient.post(`/connections/${connection.connection_id}/changes/delete`)
      setConfirmingDelete(false)
      await loadChanges()
      onChanged()
    } catch (err: any) {
      setActionError(err?.response?.data?.detail ?? 'Could not submit the delete request.')
    } finally {
      setSubmitting(false)
    }
  }

  const approveChange = async (changeId: string) => {
    setBusyChangeId(changeId)
    setActionError(null)
    try {
      await apiClient.post(`/connections/${connection.connection_id}/changes/${changeId}/approve`)
      await loadChanges()
      onChanged()
    } catch (err: any) {
      setActionError(err?.response?.data?.detail ?? 'Could not approve this change.')
    } finally {
      setBusyChangeId(null)
    }
  }

  const submitReject = async () => {
    if (!rejectingChangeId) return
    const id = rejectingChangeId
    setBusyChangeId(id)
    setActionError(null)
    try {
      await apiClient.post(`/connections/${connection.connection_id}/changes/${id}/reject`, { reason: rejectReason })
      setRejectingChangeId(null)
      setRejectReason('')
      await loadChanges()
      onChanged()
    } catch (err: any) {
      setActionError(err?.response?.data?.detail ?? 'Could not reject this change.')
    } finally {
      setBusyChangeId(null)
    }
  }

  return (
    <div className={`mt-2 rounded-md border border-line px-3 py-2 ${connection.is_hidden ? 'opacity-50' : ''}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm">
          {connection.connection_name && <div className="text-sm font-medium text-ink">{connection.connection_name}</div>}
          {connection.connection_mode === 'direct' ? (
            <span className="font-mono text-xs text-ink">
              {connection.db_type === 'snowflake' ? (
                <>
                  snowflake · {connection.host}/{connection.database_name}/{connection.snowflake_schema}
                  {connection.snowflake_warehouse && ` (warehouse: ${connection.snowflake_warehouse})`}
                </>
              ) : connection.db_type === 'mongodb' ? (
                <>
                  mongodb{connection.mongodb_srv ? '+srv' : ''} · {connection.host}
                  {!connection.mongodb_srv && `:${connection.port}`}/{connection.database_name}
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
        <div className="flex items-center gap-2">
          <span className={`text-xs ${revoked ? 'font-medium text-red-600' : 'text-ink-soft'}`}>
            {connection.connection_status}
            {connection.last_tested_at ? ` · tested ${new Date(connection.last_tested_at).toLocaleString()}` : ''}
          </span>
          <button onClick={() => onToggleHidden(connection)} className="text-xs font-medium text-ink-soft hover:text-ink hover:underline">
            {connection.is_hidden ? 'Unhide' : 'Hide'}
          </button>
        </div>
      </div>
      {connection.connection_mode === 'direct' && connection.db_type === 'snowflake' && (
        <p className="mt-2 text-xs text-amber-700">
          Snowflake cost notice: testing or discovering schema here may consume Snowflake compute credits.
        </p>
      )}

      {pending && (
        <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <div className="font-semibold">{describeConnectionChange(pending)} — awaiting approval from {approverNames}</div>
          {canApproveChange && pending.requested_by !== user?.user_id ? (
            <div className="mt-2 flex gap-2">
              <button onClick={() => approveChange(pending.change_id)} disabled={busyChangeId === pending.change_id} className="rounded-md border border-transparent bg-accent px-2.5 py-1 text-xs font-medium text-white hover:bg-accent-ink disabled:opacity-60">
                {busyChangeId === pending.change_id ? 'Approving…' : 'Approve'}
              </button>
              <button onClick={() => setRejectingChangeId(pending.change_id)} disabled={busyChangeId === pending.change_id} className="rounded-md border border-line bg-white px-2.5 py-1 text-xs font-medium text-ink hover:bg-bg disabled:opacity-60">
                Reject
              </button>
            </div>
          ) : (
            <p className="mt-1 text-amber-700">
              {pending.requested_by === user?.user_id
                ? 'You submitted this change — a different authorized approver must approve or reject it.'
                : 'Awaiting review by an authorized approver.'}
            </p>
          )}
        </div>
      )}

      {connection.connection_mode === 'direct' && canManage && !revoked && (
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
          {!pending && (
            <>
              <button
                onClick={() => setEditing((v) => !v)}
                className="rounded-md border border-line px-2 py-1 text-xs font-medium text-ink hover:bg-bg"
              >
                {editing ? 'Cancel edit' : 'Rename / edit'}
              </button>
              <button
                onClick={() => setConfirmingDisconnect(true)}
                className="rounded-md border border-red-300 bg-white px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50"
              >
                Disconnect
              </button>
              <button
                onClick={() => setConfirmingDelete(true)}
                className="rounded-md border border-red-300 bg-white px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50"
              >
                Delete
              </button>
            </>
          )}
          {testResult && (
            <span className={`text-xs ${testResult.success ? 'text-accent-ink' : 'text-red-600'}`}>
              {testResult.success ? '✓' : '✗'} {testResult.detail}
            </span>
          )}
          {actionError && <span className="text-xs text-red-600">{actionError}</span>}
        </div>
      )}
      {revoked && <p className="mt-2 text-xs text-ink-soft">Disconnected — kept for history, no longer tested or used.</p>}

      {editing && (
        <div className="mt-2 space-y-2 rounded-md border border-line bg-bg p-2">
          <input
            placeholder="Connection name (optional label)"
            value={editForm.connection_name}
            onChange={(e) => setEditForm({ ...editForm, connection_name: e.target.value })}
            className="w-full rounded-md border border-line px-2 py-1 text-xs"
          />
          <div className="flex gap-2">
            <input
              placeholder="Host"
              value={editForm.host}
              onChange={(e) => setEditForm({ ...editForm, host: e.target.value })}
              className="flex-1 rounded-md border border-line px-2 py-1 text-xs"
            />
            {showsPort && (
              <input
                type="number"
                placeholder="Port"
                value={editForm.port}
                onChange={(e) => setEditForm({ ...editForm, port: Number(e.target.value) })}
                className="w-24 rounded-md border border-line px-2 py-1 text-xs"
              />
            )}
          </div>
          <div className="flex gap-2">
            <input
              placeholder="Database name"
              value={editForm.database_name}
              onChange={(e) => setEditForm({ ...editForm, database_name: e.target.value })}
              className="flex-1 rounded-md border border-line px-2 py-1 text-xs"
            />
            <input
              placeholder="Username"
              value={editForm.username}
              onChange={(e) => setEditForm({ ...editForm, username: e.target.value })}
              className="flex-1 rounded-md border border-line px-2 py-1 text-xs"
            />
          </div>
          <input
            type="password"
            placeholder="New password (leave blank to keep current)"
            value={editForm.password}
            onChange={(e) => setEditForm({ ...editForm, password: e.target.value })}
            className="w-full rounded-md border border-line px-2 py-1 text-xs"
          />
          <div className="flex items-center gap-2">
            <button
              onClick={submitEdit}
              disabled={submitting || Object.keys(editPayload).length === 0}
              className="rounded-md border border-transparent bg-accent px-2.5 py-1 text-xs font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:bg-bg disabled:text-ink-faint"
            >
              {submitting ? 'Submitting…' : 'Submit for approval'}
            </button>
            <span className="text-[11px] text-ink-soft">Goes to {approverNames} — you can't approve your own change.</span>
          </div>
        </div>
      )}

      {recentDecided.length > 0 && (
        <div className="mt-2 space-y-0.5">
          {recentDecided.map((c) => (
            <div key={c.change_id} className="text-[11px] text-ink-soft">
              {new Date(c.requested_at).toLocaleDateString()} — {describeConnectionChange(c)}:{' '}
              <span className={c.approval_status === 'approved' ? 'font-medium text-accent-ink' : 'font-medium text-red-600'}>
                {c.approval_status === 'approved' ? 'approved' : 'rejected'}
              </span>
              {c.approval_status === 'rejected' && c.rejected_reason ? ` (${c.rejected_reason})` : ''}
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={confirmingDisconnect}
        title="Disconnect this connection"
        message={`Request disconnecting this connection? It goes to ${approverNames} for approval — you won't be able to approve your own request. Once approved, this connection stops being tested and its data is no longer discovered, but its history is kept.`}
        confirmLabel="Request disconnect"
        danger
        onConfirm={requestDisconnect}
        onCancel={() => setConfirmingDisconnect(false)}
      />
      <ConfirmDialog
        open={confirmingDelete}
        title="Permanently delete this connection"
        message={`Request permanently deleting this connection? It goes to ${approverNames} for approval — you won't be able to approve your own request. Once approved this cannot be undone: the connection and its own change history are removed for good (discovered tables and control mappings that came from it are not affected). Prefer "Disconnect" if you just want to stop using it while keeping the record.`}
        confirmLabel="Request deletion"
        danger
        onConfirm={requestDelete}
        onCancel={() => setConfirmingDelete(false)}
      />
      <ConfirmDialog
        open={rejectingChangeId !== null}
        title="Reject this change"
        message="Reject this connection change? It will never take effect — the person who submitted it can see why and resubmit if appropriate."
        confirmLabel="Reject"
        reasonRequired
        reasonValue={rejectReason}
        onReasonChange={setRejectReason}
        reasonPlaceholder="Why is this change being rejected?"
        onConfirm={submitReject}
        onCancel={() => {
          setRejectingChangeId(null)
          setRejectReason('')
        }}
      />
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

function DataSourceCard({
  source,
  gateways,
  canManage,
  canApproveChange,
}: {
  source: DataSourceOut
  gateways: GatewayOut[]
  canManage: boolean
  canApproveChange: boolean
}) {
  const [connections, setConnections] = useState<DataConnectionOut[]>([])
  const [entities, setEntities] = useState<DataEntityOut[]>([])
  const [eligibleApprovers, setEligibleApprovers] = useState<EligibleApproverOut[]>([])
  const [showHiddenConnections, setShowHiddenConnections] = useState(false)
  const [showHiddenEntities, setShowHiddenEntities] = useState(false)
  const [mode, setMode] = useState<'gateway' | 'direct'>('gateway')
  const [selectedGatewayId, setSelectedGatewayId] = useState('')
  const [provider, setProvider] = useState<Provider | ''>('')
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
    mongodb_srv: true,
  })
  const [isAddingConnection, setIsAddingConnection] = useState(false)
  const [connectionError, setConnectionError] = useState<string | null>(null)

  const load = () => {
    apiClient.get<DataConnectionOut[]>(`/data-sources/${source.data_source_id}/connections`).then((res) => setConnections(res.data))
    apiClient.get<DataEntityOut[]>(`/data-sources/${source.data_source_id}/entities`).then((res) => setEntities(res.data))
    apiClient
      .get<EligibleApproverOut[]>(`/organizations/${source.organization_id}/data-sources/eligible-approvers`)
      .then((res) => setEligibleApprovers(res.data))
  }

  useEffect(load, [source.data_source_id])

  const toggleConnectionHidden = async (connection: DataConnectionOut) => {
    await apiClient.patch(`/connections/${connection.connection_id}/hidden`, { hidden: !connection.is_hidden })
    load()
  }

  const toggleEntityHidden = async (entity: DataEntityOut) => {
    await apiClient.patch(`/data-sources/${source.data_source_id}/entities/${entity.entity_id}/hidden`, { hidden: !entity.is_hidden })
    load()
  }

  const [bulkBusy, setBulkBusy] = useState(false)
  const setAllConnectionsHidden = async (hidden: boolean) => {
    setBulkBusy(true)
    try {
      await apiClient.patch(`/data-sources/${source.data_source_id}/connections/hidden`, { hidden })
      load()
    } finally {
      setBulkBusy(false)
    }
  }
  const setAllEntitiesHidden = async (hidden: boolean) => {
    setBulkBusy(true)
    try {
      await apiClient.patch(`/data-sources/${source.data_source_id}/entities/hidden`, { hidden })
      load()
    } finally {
      setBulkBusy(false)
    }
  }

  const visibleConnections = connections.filter((c) => showHiddenConnections || !c.is_hidden)
  const hiddenConnectionCount = connections.length - visibleConnections.length
  const visibleEntities = entities.filter((e) => showHiddenEntities || !e.is_hidden)
  const hiddenEntityCount = entities.length - visibleEntities.length

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
          mongodb_srv: true,
        })
        setProvider('')
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
  // MongoDB needs no port only while using mongodb+srv:// (the default —
  // DNS resolves the real hosts/ports, same reason Snowflake has no port).
  const directFormValid =
    direct.host &&
    direct.username &&
    direct.password &&
    (direct.db_type === 'snowflake' || (direct.db_type === 'mongodb' && direct.mongodb_srv) || direct.port) &&
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
        <div className="flex items-center justify-between">
          <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Connections</div>
          <div className="flex items-center gap-2">
            {visibleConnections.length > 0 && (
              <button
                onClick={() => setAllConnectionsHidden(true)}
                disabled={bulkBusy}
                className="text-xs font-medium text-ink-soft hover:text-ink hover:underline disabled:opacity-60"
              >
                Hide all
              </button>
            )}
            {hiddenConnectionCount > 0 && (
              <>
                <button
                  onClick={() => setAllConnectionsHidden(false)}
                  disabled={bulkBusy}
                  className="text-xs font-medium text-ink-soft hover:text-ink hover:underline disabled:opacity-60"
                >
                  Unhide all
                </button>
                <button
                  onClick={() => setShowHiddenConnections((v) => !v)}
                  className="text-xs font-medium text-ink-soft hover:text-ink hover:underline"
                >
                  {showHiddenConnections ? 'Hide hidden' : `Show ${hiddenConnectionCount} hidden`}
                </button>
              </>
            )}
          </div>
        </div>
        {visibleConnections.map((c) => (
          <ConnectionRow
            key={c.connection_id}
            connection={c}
            canManage={canManage}
            canApproveChange={canApproveChange}
            eligibleApprovers={eligibleApprovers}
            onDiscovered={load}
            onChanged={load}
            onToggleHidden={toggleConnectionHidden}
          />
        ))}
        {visibleConnections.length === 0 && <div className="mt-1 text-sm text-ink-soft">No connection yet.</div>}

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
              <div className="mt-2">
                <p className="text-xs text-ink-soft">
                  For a database on your own network or this machine — anything not directly reachable from the
                  internet. Install a Gateway inside that network first (it makes an outbound-only connection out to
                  this platform, so no inbound firewall port ever needs opening), then select it below. Haven't set
                  one up yet? Go to the{' '}
                  <Link to="/gateways" className="font-medium text-accent-ink hover:underline">
                    Gateways
                  </Link>{' '}
                  page.
                </p>
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
                {direct.db_type === 'mongodb' && (
                  <p className="rounded-md border border-line bg-bg px-2 py-1.5 text-xs text-ink-soft">
                    MongoDB has no fixed tables/columns — schema discovery samples up to 100 documents per collection
                    and infers field names/types from what's actually there. Nested objects appear as dotted paths
                    (e.g. <span className="font-mono">employee.department.name</span>); this is always an inferred
                    structure, never a guaranteed one.
                  </p>
                )}
                <div className="flex gap-2">
                  <select
                    value={direct.db_type}
                    onChange={(e) => {
                      const db_type = e.target.value as DirectDbType
                      setDirect({ ...direct, db_type, port: DEFAULT_PORT[db_type] })
                      setProvider('')
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
                    placeholder={
                      direct.db_type === 'snowflake'
                        ? 'Account identifier (e.g. xy12345.us-east-1)'
                        : direct.db_type === 'mongodb'
                          ? 'Cluster address (e.g. mamishi.xxxxx.mongodb.net)'
                          : 'Host (e.g. db.example.com)'
                    }
                    value={direct.host}
                    onChange={(e) => setDirect({ ...direct, host: e.target.value })}
                    className="flex-1 rounded-md border border-line px-2 py-1 text-sm"
                  />
                  {direct.db_type !== 'snowflake' && !(direct.db_type === 'mongodb' && direct.mongodb_srv) && (
                    <input
                      type="number"
                      placeholder="Port"
                      value={direct.port}
                      onChange={(e) => setDirect({ ...direct, port: Number(e.target.value) })}
                      className="w-20 rounded-md border border-line px-2 py-1 text-sm"
                    />
                  )}
                </div>

                {PROVIDERS_FOR_DB_TYPE[direct.db_type] && (
                  <div className="rounded-md border border-line bg-bg p-2">
                    <label className="flex items-center gap-2 text-xs font-medium text-ink">
                      Where is this hosted?
                      <select
                        value={provider}
                        onChange={(e) => {
                          const p = e.target.value as Provider | ''
                          setProvider(p)
                          const impliedDbType = p ? PROVIDER_DB_TYPE[p as Provider] : undefined
                          if (impliedDbType) setDirect({ ...direct, db_type: impliedDbType, port: DEFAULT_PORT[impliedDbType] })
                        }}
                        className="rounded-md border border-line px-2 py-1 text-xs"
                      >
                        <option value="">Show me where to find these values…</option>
                        {PROVIDERS_FOR_DB_TYPE[direct.db_type]!.map((p) => (
                          <option key={p} value={p}>
                            {PROVIDER_LABELS[p]}
                          </option>
                        ))}
                      </select>
                    </label>
                    {provider && (
                      <ol className="mt-2 list-decimal space-y-1 pl-4 text-[11px] text-ink-soft">
                        {PROVIDER_GUIDES[provider].map((step, i) => (
                          <li key={i}>{step}</li>
                        ))}
                      </ol>
                    )}
                  </div>
                )}
                {DB_TYPE_GUIDES[direct.db_type] && (
                  <div className="rounded-md border border-line bg-bg p-2">
                    <div className="text-xs font-medium text-ink">Where do I find these values?</div>
                    <ol className="mt-1 list-decimal space-y-1 pl-4 text-[11px] text-ink-soft">
                      {DB_TYPE_GUIDES[direct.db_type]!.map((step, i) => (
                        <li key={i}>{step}</li>
                      ))}
                    </ol>
                  </div>
                )}

                {direct.db_type === 'mongodb' && (
                  <label className="flex items-center gap-2 text-xs text-ink">
                    <input
                      type="checkbox"
                      checked={direct.mongodb_srv}
                      onChange={(e) => setDirect({ ...direct, mongodb_srv: e.target.checked })}
                    />
                    Use SRV connection (mongodb+srv://) — standard for MongoDB Atlas. Uncheck only for a self-hosted
                    deployment connected by host and port.
                  </label>
                )}
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
                          : direct.db_type === 'mongodb'
                            ? 'Database name (the target database within this cluster)'
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
          <div className="flex items-center justify-between px-4 pt-3">
            <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">
              Discovered tables ({visibleEntities.length})
            </div>
            <div className="flex items-center gap-2">
              {visibleEntities.length > 0 && (
                <button
                  onClick={() => setAllEntitiesHidden(true)}
                  disabled={bulkBusy}
                  className="text-xs font-medium text-ink-soft hover:text-ink hover:underline disabled:opacity-60"
                >
                  Hide all
                </button>
              )}
              {hiddenEntityCount > 0 && (
                <>
                  <button
                    onClick={() => setAllEntitiesHidden(false)}
                    disabled={bulkBusy}
                    className="text-xs font-medium text-ink-soft hover:text-ink hover:underline disabled:opacity-60"
                  >
                    Unhide all
                  </button>
                  <button
                    onClick={() => setShowHiddenEntities((v) => !v)}
                    className="text-xs font-medium text-ink-soft hover:text-ink hover:underline"
                  >
                    {showHiddenEntities ? 'Hide hidden' : `Show ${hiddenEntityCount} hidden`}
                  </button>
                </>
              )}
            </div>
          </div>
          {visibleEntities.map((e) => (
            <EntityRow key={e.entity_id} entity={e} onToggleHidden={toggleEntityHidden} />
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
  const canApproveChange = hasRole('Platform Super Admin', 'Audit Manager')

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
          <DataSourceCard key={s.data_source_id} source={s} gateways={gateways} canManage={canManage} canApproveChange={canApproveChange} />
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
