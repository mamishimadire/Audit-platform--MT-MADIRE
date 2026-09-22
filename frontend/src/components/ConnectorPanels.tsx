import { useEffect, useRef, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { ConfirmDialog } from './ConfirmDialog'
import type { ConnectorPreset, DataConnectionOut, DataFileOut, DataFileUploadOut } from '../types/api'

// Panels for the connection families that are not databases (see lib/connectorTypes): files the user uploads, an SFTP
// drop, a REST or SOAP API, and the ready-made Salesforce / Zoho CRM / Dynamics 365 CRM templates (REST connections).

const inputClass = 'rounded-md border border-line px-2 py-1 text-sm'
const smallInput = 'rounded-md border border-line px-2 py-1 text-xs'

function errorText(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return 'Some of the values are not valid — check the fields and try again.'
  return fallback
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function kb(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const PRESET_NOTE =
  'These templates follow each vendor\'s public API documentation and were tested against servers that answer in the documented shapes. They have not yet been run against a live tenant of that vendor, so the first connection may need a setting adjusted — every setting can be edited afterwards.'

// --- one-line summary of a connection, for the connection list ---------------------------------------------------
export function ConnectorSummary({ connection }: { connection: DataConnectionOut }) {
  const config = connection.connector_config ?? {}
  const kind = connection.db_type
  if (kind === 'file_upload') return <span className="font-mono text-xs text-ink">uploaded files</span>
  if (kind === 'sftp') {
    const pinned = text(config.host_key_sha256)
    return (
      <span className="font-mono text-xs text-ink">
        sftp · {connection.host}:{connection.port}
        {text(config.remote_path)}
        <span className="text-ink-soft"> (user: {connection.username})</span>
        <span className={pinned ? ' text-accent-ink' : ' text-amber-700'} title={pinned || undefined}>
          {pinned ? ' · server key recorded' : ' · server key not recorded yet — run Test connection'}
        </span>
      </span>
    )
  }
  const endpoints = Array.isArray(config.endpoints) ? (config.endpoints as { entity_name?: string }[]) : []
  const preset = text(config.preset)
  return (
    <span className="font-mono text-xs text-ink">
      {kind === 'soap_api' ? 'soap' : 'rest'} · {text(config.base_url)}
      <span className="text-ink-soft">
        {' '}
        ({endpoints.length} table{endpoints.length === 1 ? '' : 's'}
        {preset ? ` · ${preset.replace(/_/g, ' ')} template` : ''})
      </span>
    </span>
  )
}

// --- uploaded files: list of versions, upload, delete ----------------------------------------------------------------
export function FilePanel({ connection, canManage, onChanged }: { connection: DataConnectionOut; canManage: boolean; onChanged: () => void }) {
  const [files, setFiles] = useState<DataFileOut[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<DataFileUploadOut | null>(null)
  const [deleting, setDeleting] = useState<DataFileOut | null>(null)
  const input = useRef<HTMLInputElement>(null)

  const load = () => apiClient.get<DataFileOut[]>(`/connections/${connection.connection_id}/files`).then((res) => setFiles(res.data))
  useEffect(() => {
    load()
  }, [connection.connection_id])

  const upload = async (file: File) => {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await apiClient.post<DataFileUploadOut>(`/connections/${connection.connection_id}/files`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setResult(res.data)
      await load()
      onChanged()
    } catch (err) {
      setError(errorText(err, 'Could not upload this file.'))
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }

  const remove = async () => {
    if (!deleting) return
    try {
      await apiClient.delete(`/connections/${connection.connection_id}/files/${deleting.file_id}`)
      setDeleting(null)
      await load()
      onChanged()
    } catch (err) {
      setDeleting(null)
      setError(errorText(err, 'Could not remove this file.'))
    }
  }

  const byName = new Map<string, DataFileOut[]>()
  for (const f of files) byName.set(f.file_name, [...(byName.get(f.file_name) ?? []), f])

  return (
    <div className="mt-2 rounded-md border border-line bg-bg p-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs font-medium text-ink">Files</div>
        {canManage && (
          <div className="flex items-center gap-2">
            <input
              ref={input}
              type="file"
              accept=".csv,.tsv,.txt,.xlsx,.xlsm"
              disabled={busy}
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) upload(file)
              }}
              className="text-xs"
            />
            {busy && <span className="text-xs text-ink-soft">Uploading…</span>}
          </div>
        )}
      </div>
      <p className="mt-1 text-[11px] text-ink-soft">
        CSV, TSV or Excel (.xlsx), up to 25 MB. Each file — or each sheet of a workbook — becomes a table. Uploading a newer copy of a
        file with the same name replaces the data behind the same table, so mappings made to it keep working; earlier versions are kept.
        The platform reads up to about 500,000 cells (rows × columns) of a file; a larger one is used as far as that goes, and you are told.
      </p>
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
      {result && (
        <div className="mt-1 text-xs">
          <span className="text-accent-ink">
            ✓ {result.file_name} uploaded{result.discovered ? ' and its tables were added to the catalogue' : ''}.
          </span>
          {result.notices.map((n) => (
            <div key={n} className="mt-1 rounded-md border border-line bg-surface px-2 py-1 text-ink-soft">
              {n}
            </div>
          ))}
          {result.warnings.length > 0 && (
            <div className="mt-1 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-amber-800">
              <div className="font-semibold">This version changes what is already mapped:</div>
              <ul className="list-disc pl-4">
                {result.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
              <div className="mt-0.5">
                The catalogue was not refreshed, because that would remove the mappings to anything that disappeared. Review the
                mappings, then run "Discover schema" to accept the change.
              </div>
            </div>
          )}
        </div>
      )}
      {files.length === 0 ? (
        <div className="mt-1 text-xs text-ink-soft">No file uploaded yet.</div>
      ) : (
        <div className="mt-1 space-y-1">
          {[...byName.entries()].map(([name, versions]) => (
            <div key={name} className="text-xs">
              <div className="font-medium text-ink">{name}</div>
              {versions.map((v) => (
                <div key={v.file_id} className="flex flex-wrap items-center gap-2 pl-3 text-[11px] text-ink-soft">
                  <span className={v.is_current ? 'font-medium text-accent-ink' : ''}>{v.is_current ? 'current' : 'earlier'}</span>
                  <span>{new Date(v.uploaded_at).toLocaleString()}</span>
                  <span>{kb(v.size_bytes)}</span>
                  <span className="font-mono" title={v.sha256}>
                    sha256 {v.sha256.slice(0, 12)}…
                  </span>
                  {canManage && (
                    <button onClick={() => setDeleting(v)} className="font-medium text-red-700 hover:underline">
                      Remove
                    </button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
      <ConfirmDialog
        open={deleting !== null}
        title="Remove this file version"
        message={`Remove this version of ${deleting?.file_name ?? 'the file'}? If it is the current one, the newest earlier version takes over; if there is none, the table has no data until you upload again.`}
        confirmLabel="Remove"
        danger
        onConfirm={remove}
        onCancel={() => setDeleting(null)}
      />
    </div>
  )
}

// --- adding a connection ----------------------------------------------------------------------------------------------------
type Kind = 'file_upload' | 'sftp' | 'rest_api' | 'soap_api' | 'preset'

const KIND_LABELS: Record<Kind, string> = {
  file_upload: 'Upload CSV / Excel files',
  sftp: 'SFTP server',
  rest_api: 'REST API',
  soap_api: 'SOAP API',
  preset: 'Salesforce, Zoho CRM, Dynamics 365',
}

interface EndpointForm {
  entity_name: string
  path: string
  records_path: string
  paginationType: string
  page_size: string
  max_pages: string
  cursor_param: string
  next_cursor_path: string
  next_url_path: string
  soap_action: string
  body: string
}

const emptyEndpoint = (): EndpointForm => ({
  entity_name: '', path: '/', records_path: '', paginationType: 'none', page_size: '100', max_pages: '10',
  cursor_param: '', next_cursor_path: '', next_url_path: '', soap_action: '', body: '',
})

function endpointConfig(e: EndpointForm, soap: boolean): Record<string, unknown> {
  const out: Record<string, unknown> = { entity_name: e.entity_name.trim(), path: e.path.trim() }
  if (e.records_path.trim()) out.records_path = e.records_path.trim()
  if (soap) {
    out.body = e.body
    if (e.soap_action.trim()) out.soap_action = e.soap_action.trim()
  }
  const max_pages = Number(e.max_pages) || 10
  const page_size = Number(e.page_size) || 100
  if (e.paginationType === 'page') out.pagination = { type: 'page', page_size, max_pages }
  else if (e.paginationType === 'offset') out.pagination = { type: 'offset', page_size, max_pages }
  else if (e.paginationType === 'cursor') out.pagination = { type: 'cursor', cursor_param: e.cursor_param.trim(), next_cursor_path: e.next_cursor_path.trim(), max_pages }
  else if (e.paginationType === 'next_url') out.pagination = { type: 'next_url', next_url_path: e.next_url_path.trim(), max_pages }
  else if (e.paginationType === 'link_header') out.pagination = { type: 'link_header', max_pages }
  return out
}

export function AddConnectorForm({ dataSourceId, onCreated }: { dataSourceId: string; onCreated: () => void }) {
  const [kind, setKind] = useState<Kind>('file_upload')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // SFTP
  const [sftp, setSftp] = useState({ host: '', port: '22', username: '', remote_path: '', table_name: '', auth: 'password', password: '', private_key: '', passphrase: '', host_key: '' })
  // REST / SOAP
  const [api, setApi] = useState({
    base_url: '', authType: 'none', in: 'header', keyName: 'X-API-Key', scheme: '', username: '', token_url: '', scope: '',
    secret_api_key: '', secret_token: '', secret_password: '', secret_client_id: '', secret_client_secret: '', secret_refresh_token: '',
  })
  const [endpoints, setEndpoints] = useState<EndpointForm[]>([emptyEndpoint()])
  const [asJson, setAsJson] = useState(false)
  const [jsonText, setJsonText] = useState('')
  // Presets
  const [presets, setPresets] = useState<ConnectorPreset[]>([])
  const [presetKey, setPresetKey] = useState('')
  const [presetParams, setPresetParams] = useState<Record<string, string>>({})
  const [presetSecrets, setPresetSecrets] = useState<Record<string, string>>({})

  useEffect(() => {
    if (kind === 'preset' && presets.length === 0) {
      apiClient.get<ConnectorPreset[]>('/connector-presets').then((res) => {
        setPresets(res.data)
        if (res.data.length > 0) setPresetKey(res.data[0].key)
      })
    }
  }, [kind, presets.length])

  const soap = kind === 'soap_api'
  const preset = presets.find((p) => p.key === presetKey)

  const apiConfig = (): Record<string, unknown> => {
    const auth: Record<string, unknown> = { type: api.authType }
    if (api.authType === 'api_key') Object.assign(auth, { in: api.in, name: api.keyName })
    if (api.authType === 'basic') auth.username = api.username
    if (api.authType.startsWith('oauth2')) {
      auth.token_url = api.token_url
      if (api.scope) auth.scope = api.scope
    }
    if (api.scheme && (api.authType === 'bearer' || api.authType.startsWith('oauth2'))) auth.scheme = api.scheme
    return { base_url: api.base_url.trim(), auth, endpoints: endpoints.map((e) => endpointConfig(e, soap)) }
  }
  const apiSecrets = (): Record<string, string> => {
    const s: Record<string, string> = {}
    if (api.authType === 'api_key') s.api_key = api.secret_api_key
    if (api.authType === 'bearer') s.token = api.secret_token
    if (api.authType === 'basic') s.password = api.secret_password
    if (api.authType.startsWith('oauth2')) Object.assign(s, { client_id: api.secret_client_id, client_secret: api.secret_client_secret })
    if (api.authType === 'oauth2_refresh_token') s.refresh_token = api.secret_refresh_token
    return s
  }

  const build = (): Record<string, unknown> | string => {
    const base: Record<string, unknown> = { db_type: kind === 'preset' ? 'rest_api' : kind }
    if (name.trim()) base.connection_name = name.trim()
    if (kind === 'file_upload') return base
    if (kind === 'sftp') {
      const config: Record<string, unknown> = { host: sftp.host.trim(), port: Number(sftp.port) || 22, username: sftp.username.trim(), remote_path: sftp.remote_path.trim(), auth: sftp.auth }
      if (sftp.table_name.trim()) config.table_name = sftp.table_name.trim()
      if (sftp.host_key.trim()) config.host_key_sha256 = sftp.host_key.trim()
      const secrets = sftp.auth === 'password' ? { password: sftp.password } : { private_key: sftp.private_key, ...(sftp.passphrase ? { passphrase: sftp.passphrase } : {}) }
      return { ...base, config, secrets }
    }
    if (kind === 'preset') {
      if (!preset) return 'Choose a template.'
      const params: Record<string, unknown> = {}
      for (const p of preset.params) {
        const v = (presetParams[p.name] ?? '').trim()
        if (!v) continue
        params[p.name] = p.kind === 'list' ? v.split(',').map((x) => x.trim()).filter(Boolean) : v
      }
      return { ...base, preset: preset.key, preset_params: params, secrets: presetSecrets }
    }
    if (asJson) {
      try {
        return { ...base, config: JSON.parse(jsonText), secrets: apiSecrets() }
      } catch {
        return 'The settings are not valid JSON.'
      }
    }
    return { ...base, config: apiConfig(), secrets: apiSecrets() }
  }

  const submit = async () => {
    if (busy) return
    const body = build()
    if (typeof body === 'string') {
      setError(body)
      return
    }
    setBusy(true)
    setError(null)
    try {
      await apiClient.post(`/data-sources/${dataSourceId}/connections/connector`, body)
      setName('')
      setSftp({ host: '', port: '22', username: '', remote_path: '', table_name: '', auth: 'password', password: '', private_key: '', passphrase: '', host_key: '' })
      setPresetSecrets({})
      onCreated()
    } catch (err) {
      setError(errorText(err, 'Could not add this connection.'))
    } finally {
      setBusy(false)
    }
  }

  const setEndpoint = (i: number, patch: Partial<EndpointForm>) => setEndpoints((list) => list.map((e, j) => (j === i ? { ...e, ...patch } : e)))

  return (
    <div className="mt-2 space-y-2">
      <div className="flex flex-wrap gap-1.5 text-xs">
        {(Object.keys(KIND_LABELS) as Kind[]).map((k) => (
          <button key={k} onClick={() => { setKind(k); setError(null) }} className={`rounded-md border px-2 py-1 ${kind === k ? 'border-accent bg-accent-soft font-medium text-accent-ink' : 'border-line text-ink-soft hover:bg-bg'}`}>
            {KIND_LABELS[k]}
          </button>
        ))}
      </div>
      <p className="text-[11px] text-ink-soft">
        {kind === 'file_upload' && 'Upload CSV or Excel files yourself, then map them like any database table. Nothing to install and no credentials.'}
        {kind === 'sftp' && 'The platform fetches the newest file matching a path or pattern from your SFTP server, on every run. The server must be reachable from the public internet (for one on your own network, use a Gateway). The first successful test records the server\'s host key; a different key later is refused.'}
        {(kind === 'rest_api' || kind === 'soap_api') && 'Each endpoint you define becomes a table. Credentials are stored encrypted and never shown again. The API must be reachable over HTTPS from the public internet; addresses inside a private network are refused.'}
        {kind === 'preset' && 'Ready-made settings for a well-known CRM. You give the few facts it needs; everything can be edited afterwards.'}
      </p>
      <input placeholder="Connection name (optional label)" value={name} onChange={(e) => setName(e.target.value)} className={`w-full ${inputClass}`} />

      {kind === 'sftp' && (
        <div className="space-y-2">
          <div className="flex gap-2">
            <input placeholder="Host (e.g. sftp.acme.com)" value={sftp.host} onChange={(e) => setSftp({ ...sftp, host: e.target.value })} className={`flex-1 ${inputClass}`} />
            <input type="number" placeholder="Port" value={sftp.port} onChange={(e) => setSftp({ ...sftp, port: e.target.value })} className={`w-24 ${inputClass}`} />
          </div>
          <div className="flex gap-2">
            <input placeholder="Username" value={sftp.username} onChange={(e) => setSftp({ ...sftp, username: e.target.value })} className={`flex-1 ${inputClass}`} />
            <input placeholder="Table name (optional)" value={sftp.table_name} onChange={(e) => setSftp({ ...sftp, table_name: e.target.value })} className={`flex-1 ${inputClass}`} />
          </div>
          <input placeholder="File or pattern, e.g. /exports/payroll_*.csv (the newest match is used)" value={sftp.remote_path} onChange={(e) => setSftp({ ...sftp, remote_path: e.target.value })} className={`w-full ${inputClass}`} />
          <div className="flex items-center gap-2 text-xs">
            <label className="flex items-center gap-1"><input type="radio" checked={sftp.auth === 'password'} onChange={() => setSftp({ ...sftp, auth: 'password' })} /> Password</label>
            <label className="flex items-center gap-1"><input type="radio" checked={sftp.auth === 'private_key'} onChange={() => setSftp({ ...sftp, auth: 'private_key' })} /> Private key</label>
          </div>
          {sftp.auth === 'password' ? (
            <input type="password" placeholder="Password" value={sftp.password} onChange={(e) => setSftp({ ...sftp, password: e.target.value })} className={`w-full ${inputClass}`} />
          ) : (
            <>
              <textarea placeholder="Private key (PEM, starts with -----BEGIN … PRIVATE KEY-----)" value={sftp.private_key} onChange={(e) => setSftp({ ...sftp, private_key: e.target.value })} rows={4} className={`w-full font-mono text-xs ${inputClass}`} />
              <input type="password" placeholder="Key passphrase (only if the key is encrypted)" value={sftp.passphrase} onChange={(e) => setSftp({ ...sftp, passphrase: e.target.value })} className={`w-full ${inputClass}`} />
            </>
          )}
          <input placeholder="Server key fingerprint (optional, SHA256:… as shown by ssh-keygen -lf) — otherwise the first test records it" value={sftp.host_key} onChange={(e) => setSftp({ ...sftp, host_key: e.target.value })} className={`w-full ${inputClass}`} />
        </div>
      )}

      {(kind === 'rest_api' || kind === 'soap_api') && (
        <div className="space-y-2">
          <input placeholder="Base address, e.g. https://api.example.com/v2" value={api.base_url} onChange={(e) => setApi({ ...api, base_url: e.target.value })} className={`w-full ${inputClass}`} />
          <div className="flex flex-wrap gap-2">
            <select value={api.authType} onChange={(e) => setApi({ ...api, authType: e.target.value })} className={inputClass}>
              <option value="none">No authentication</option>
              <option value="api_key">API key</option>
              <option value="bearer">Bearer token</option>
              <option value="basic">Username and password</option>
              <option value="oauth2_client_credentials">OAuth2 — client credentials</option>
              <option value="oauth2_refresh_token">OAuth2 — refresh token</option>
            </select>
            {api.authType === 'api_key' && (
              <>
                <select value={api.in} onChange={(e) => setApi({ ...api, in: e.target.value })} className={inputClass}>
                  <option value="header">sent in a header</option>
                  <option value="query">sent in the address</option>
                </select>
                <input placeholder="Header / parameter name" value={api.keyName} onChange={(e) => setApi({ ...api, keyName: e.target.value })} className={`w-44 ${inputClass}`} />
                <input type="password" placeholder="API key" value={api.secret_api_key} onChange={(e) => setApi({ ...api, secret_api_key: e.target.value })} className={`flex-1 ${inputClass}`} />
              </>
            )}
            {api.authType === 'bearer' && <input type="password" placeholder="Token" value={api.secret_token} onChange={(e) => setApi({ ...api, secret_token: e.target.value })} className={`flex-1 ${inputClass}`} />}
            {api.authType === 'basic' && (
              <>
                <input placeholder="Username" value={api.username} onChange={(e) => setApi({ ...api, username: e.target.value })} className={`flex-1 ${inputClass}`} />
                <input type="password" placeholder="Password" value={api.secret_password} onChange={(e) => setApi({ ...api, secret_password: e.target.value })} className={`flex-1 ${inputClass}`} />
              </>
            )}
            {api.authType.startsWith('oauth2') && (
              <>
                <input placeholder="Token address (https://…)" value={api.token_url} onChange={(e) => setApi({ ...api, token_url: e.target.value })} className={`w-full ${inputClass}`} />
                <input placeholder="Client id" value={api.secret_client_id} onChange={(e) => setApi({ ...api, secret_client_id: e.target.value })} className={`flex-1 ${inputClass}`} />
                <input type="password" placeholder="Client secret" value={api.secret_client_secret} onChange={(e) => setApi({ ...api, secret_client_secret: e.target.value })} className={`flex-1 ${inputClass}`} />
                {api.authType === 'oauth2_refresh_token' && <input type="password" placeholder="Refresh token" value={api.secret_refresh_token} onChange={(e) => setApi({ ...api, secret_refresh_token: e.target.value })} className={`w-full ${inputClass}`} />}
                <input placeholder="Scope (optional)" value={api.scope} onChange={(e) => setApi({ ...api, scope: e.target.value })} className={`flex-1 ${inputClass}`} />
              </>
            )}
            {(api.authType === 'bearer' || api.authType.startsWith('oauth2')) && (
              <input placeholder="Header word (default Bearer)" value={api.scheme} onChange={(e) => setApi({ ...api, scheme: e.target.value })} className={`w-44 ${inputClass}`} />
            )}
          </div>

          <div className="flex items-center justify-between">
            <div className="text-xs font-medium text-ink">Endpoints — each becomes a table</div>
            <label className="flex items-center gap-1 text-[11px] text-ink-soft">
              <input type="checkbox" checked={asJson} onChange={(e) => { setAsJson(e.target.checked); if (e.target.checked) setJsonText(JSON.stringify(apiConfig(), null, 2)) }} /> Edit all settings as JSON
            </label>
          </div>
          {asJson ? (
            <textarea value={jsonText} onChange={(e) => setJsonText(e.target.value)} rows={14} className={`w-full font-mono text-xs ${inputClass}`} />
          ) : (
            endpoints.map((e, i) => (
              <div key={i} className="space-y-1.5 rounded-md border border-line bg-bg p-2">
                <div className="flex gap-2">
                  <input placeholder="Table name" value={e.entity_name} onChange={(ev) => setEndpoint(i, { entity_name: ev.target.value })} className={`flex-1 ${smallInput}`} />
                  <input placeholder="Path, e.g. /invoices" value={e.path} onChange={(ev) => setEndpoint(i, { path: ev.target.value })} className={`flex-1 ${smallInput}`} />
                  {endpoints.length > 1 && (
                    <button onClick={() => setEndpoints((l) => l.filter((_, j) => j !== i))} className="text-xs font-medium text-red-700 hover:underline">Remove</button>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  <input placeholder={soap ? 'Records path, e.g. Envelope.Body.Response.Item' : 'Records path, e.g. data.items (blank if the answer is the list)'} value={e.records_path} onChange={(ev) => setEndpoint(i, { records_path: ev.target.value })} className={`min-w-[14rem] flex-1 ${smallInput}`} />
                  {!soap && (
                    <select value={e.paginationType} onChange={(ev) => setEndpoint(i, { paginationType: ev.target.value })} className={smallInput}>
                      <option value="none">No paging</option>
                      <option value="page">Page number</option>
                      <option value="offset">Offset / limit</option>
                      <option value="cursor">Cursor</option>
                      <option value="next_url">Next address in the answer</option>
                      <option value="link_header">Link header</option>
                    </select>
                  )}
                </div>
                {!soap && (e.paginationType === 'page' || e.paginationType === 'offset') && (
                  <div className="flex gap-2 text-xs"><input type="number" placeholder="Page size" value={e.page_size} onChange={(ev) => setEndpoint(i, { page_size: ev.target.value })} className={`w-28 ${smallInput}`} /><input type="number" placeholder="Max pages" value={e.max_pages} onChange={(ev) => setEndpoint(i, { max_pages: ev.target.value })} className={`w-28 ${smallInput}`} /></div>
                )}
                {!soap && e.paginationType === 'cursor' && (
                  <div className="flex gap-2"><input placeholder="Cursor parameter" value={e.cursor_param} onChange={(ev) => setEndpoint(i, { cursor_param: ev.target.value })} className={`flex-1 ${smallInput}`} /><input placeholder="Where the next cursor is in the answer, e.g. meta.next" value={e.next_cursor_path} onChange={(ev) => setEndpoint(i, { next_cursor_path: ev.target.value })} className={`flex-1 ${smallInput}`} /></div>
                )}
                {!soap && e.paginationType === 'next_url' && (
                  <input placeholder="Where the next address is in the answer, e.g. links.next" value={e.next_url_path} onChange={(ev) => setEndpoint(i, { next_url_path: ev.target.value })} className={`w-full ${smallInput}`} />
                )}
                {soap && (
                  <>
                    <input placeholder="SOAP action (optional)" value={e.soap_action} onChange={(ev) => setEndpoint(i, { soap_action: ev.target.value })} className={`w-full ${smallInput}`} />
                    <textarea placeholder="Request envelope (XML). Refer to a stored secret as {{secret.name}}." value={e.body} onChange={(ev) => setEndpoint(i, { body: ev.target.value })} rows={5} className={`w-full font-mono ${smallInput}`} />
                  </>
                )}
              </div>
            ))
          )}
          {!asJson && <button onClick={() => setEndpoints((l) => [...l, emptyEndpoint()])} className="text-xs font-medium text-accent-ink hover:underline">+ Add an endpoint</button>}
        </div>
      )}

      {kind === 'preset' && (
        <div className="space-y-2">
          <select value={presetKey} onChange={(e) => { setPresetKey(e.target.value); setPresetParams({}); setPresetSecrets({}) }} className={inputClass}>
            {presets.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
          {preset && (
            <>
              <p className="text-[11px] text-ink-soft">{preset.description}</p>
              {preset.params.map((p) => (
                <div key={p.name}>
                  {p.kind === 'choice' ? (
                    <select value={presetParams[p.name] ?? String(p.default ?? '')} onChange={(e) => setPresetParams({ ...presetParams, [p.name]: e.target.value })} className={`w-full ${inputClass}`} title={p.help}>
                      {p.choices.map((c) => <option key={c} value={c}>{p.label}: {c}</option>)}
                    </select>
                  ) : (
                    <input
                      placeholder={`${p.label}${p.required ? '' : ' (optional)'}${p.kind === 'list' ? ' — comma separated' : ''}`}
                      value={presetParams[p.name] ?? ''}
                      onChange={(e) => setPresetParams({ ...presetParams, [p.name]: e.target.value })}
                      className={`w-full ${inputClass}`}
                      title={p.help}
                    />
                  )}
                  {p.help && <div className="text-[11px] text-ink-soft">{p.help}</div>}
                </div>
              ))}
              {preset.secrets.map((s) => (
                <input key={s.name} type="password" placeholder={s.label} value={presetSecrets[s.name] ?? ''} onChange={(e) => setPresetSecrets({ ...presetSecrets, [s.name]: e.target.value })} className={`w-full ${inputClass}`} />
              ))}
              <p className="text-[11px] text-amber-700">{PRESET_NOTE}</p>
            </>
          )}
        </div>
      )}

      <button onClick={submit} disabled={busy} className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60">
        {busy ? 'Adding…' : 'Add connection'}
      </button>
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  )
}

// --- editing one (goes through the same independent approval as every connection edit) ----------------------------------
const SECRET_FIELDS: Record<string, { name: string; label: string }[]> = {
  api_key: [{ name: 'api_key', label: 'API key' }],
  bearer: [{ name: 'token', label: 'Token' }],
  basic: [{ name: 'password', label: 'Password' }],
  oauth2_client_credentials: [{ name: 'client_id', label: 'Client id' }, { name: 'client_secret', label: 'Client secret' }],
  oauth2_refresh_token: [{ name: 'client_id', label: 'Client id' }, { name: 'client_secret', label: 'Client secret' }, { name: 'refresh_token', label: 'Refresh token' }],
}

export function ConnectorEditForm({
  connection, approverNames, submitting, onSubmit,
}: {
  connection: DataConnectionOut
  approverNames: string
  submitting: boolean
  onSubmit: (payload: Record<string, unknown>) => void
}) {
  const config = connection.connector_config ?? {}
  const [name, setName] = useState(connection.connection_name ?? '')
  const [host, setHost] = useState(connection.host ?? '')
  const [port, setPort] = useState(String(connection.port ?? 22))
  const [username, setUsername] = useState(connection.username ?? '')
  const [remotePath, setRemotePath] = useState(text(config.remote_path))
  const [tableName, setTableName] = useState(text(config.table_name))
  const [hostKey, setHostKey] = useState(text(config.host_key_sha256))
  const [settingsJson, setSettingsJson] = useState(JSON.stringify(config, null, 2))
  const [newSecrets, setNewSecrets] = useState<Record<string, string>>({})
  const [sftpAuth, setSftpAuth] = useState(text(config.auth) || 'password')
  const [error, setError] = useState<string | null>(null)

  const authType = text((config.auth as { type?: string } | undefined)?.type)
  const kind = connection.db_type
  const secretFields = kind === 'sftp' ? (sftpAuth === 'password' ? [{ name: 'password', label: 'New password' }] : [{ name: 'private_key', label: 'New private key' }, { name: 'passphrase', label: 'Key passphrase (if any)' }]) : (SECRET_FIELDS[authType] ?? [])

  const build = (): Record<string, unknown> | null => {
    const payload: Record<string, unknown> = {}
    if (name !== (connection.connection_name ?? '')) payload.connection_name = name
    if (kind === 'sftp') {
      if (host !== (connection.host ?? '')) payload.host = host
      if (Number(port) !== connection.port) payload.port = Number(port)
      if (username !== (connection.username ?? '')) payload.username = username
      const changed: Record<string, unknown> = {}
      if (remotePath !== text(config.remote_path)) changed.remote_path = remotePath
      if (tableName !== text(config.table_name)) changed.table_name = tableName || null
      if (hostKey !== text(config.host_key_sha256)) changed.host_key_sha256 = hostKey || null
      if (sftpAuth !== (text(config.auth) || 'password')) changed.auth = sftpAuth
      if (Object.keys(changed).length > 0) payload.connector_config = changed
    } else if (kind === 'rest_api' || kind === 'soap_api') {
      if (settingsJson !== JSON.stringify(config, null, 2)) {
        try {
          payload.connector_config = JSON.parse(settingsJson)
        } catch {
          setError('The settings are not valid JSON.')
          return null
        }
      }
    }
    const filled = Object.fromEntries(Object.entries(newSecrets).filter(([, v]) => v))
    if (Object.keys(filled).length > 0) payload.secrets = filled
    return payload
  }

  const submit = () => {
    setError(null)
    const payload = build()
    if (payload === null) return
    if (Object.keys(payload).length === 0) {
      setError('Nothing has changed.')
      return
    }
    onSubmit(payload)
  }

  return (
    <div className="mt-2 space-y-2 rounded-md border border-line bg-bg p-2">
      <input placeholder="Connection name (optional label)" value={name} onChange={(e) => setName(e.target.value)} className={`w-full ${smallInput}`} />
      {kind === 'file_upload' && <p className="text-[11px] text-ink-soft">A file connection has nothing to configure but its name. Upload, replace or remove files from the Files panel.</p>}
      {kind === 'sftp' && (
        <>
          <div className="flex gap-2">
            <input placeholder="Host" value={host} onChange={(e) => setHost(e.target.value)} className={`flex-1 ${smallInput}`} />
            <input type="number" placeholder="Port" value={port} onChange={(e) => setPort(e.target.value)} className={`w-24 ${smallInput}`} />
            <input placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} className={`flex-1 ${smallInput}`} />
          </div>
          <input placeholder="File or pattern" value={remotePath} onChange={(e) => setRemotePath(e.target.value)} className={`w-full ${smallInput}`} />
          <div className="flex gap-2">
            <input placeholder="Table name (optional)" value={tableName} onChange={(e) => setTableName(e.target.value)} className={`flex-1 ${smallInput}`} />
            <select value={sftpAuth} onChange={(e) => setSftpAuth(e.target.value)} className={smallInput}>
              <option value="password">Password</option>
              <option value="private_key">Private key</option>
            </select>
          </div>
          <input placeholder="Recorded server key fingerprint" value={hostKey} onChange={(e) => setHostKey(e.target.value)} className={`w-full font-mono ${smallInput}`} />
          <p className="text-[11px] text-ink-soft">Moving to another host or port forgets the recorded server key unless you enter the new one here; the next test records it.</p>
        </>
      )}
      {(kind === 'rest_api' || kind === 'soap_api') && (
        <>
          <div className="text-[11px] text-ink-soft">Settings (never contains a credential — refer to a stored secret as {'{{secret.name}}'}). The changed top-level items replace the current ones.</div>
          <textarea value={settingsJson} onChange={(e) => setSettingsJson(e.target.value)} rows={14} className={`w-full font-mono ${smallInput}`} />
        </>
      )}
      {kind !== 'file_upload' && secretFields.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[11px] text-ink-soft">Replace credentials (leave blank to keep the current ones). Filling any replaces ALL stored credentials, so fill every one this connection needs.</div>
          {secretFields.map((f) => (
            <input key={f.name} type="password" placeholder={f.label} value={newSecrets[f.name] ?? ''} onChange={(e) => setNewSecrets({ ...newSecrets, [f.name]: e.target.value })} className={`w-full ${smallInput}`} />
          ))}
        </div>
      )}
      {error && <p className="text-xs text-red-600">{error}</p>}
      <div className="flex items-center gap-2">
        <button onClick={submit} disabled={submitting} className="rounded-md border border-transparent bg-accent px-2.5 py-1 text-xs font-medium text-white hover:bg-accent-ink disabled:opacity-60">
          {submitting ? 'Submitting…' : 'Submit for approval'}
        </button>
        <span className="text-[11px] text-ink-soft">Goes to {approverNames} — you can't approve your own change. The connection is tested again before it is used.</span>
      </div>
    </div>
  )
}
