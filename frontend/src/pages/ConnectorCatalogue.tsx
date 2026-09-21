import { Link } from 'react-router-dom'

interface Category {
  code: string
  color: string
  title: string
  subtitle: string
  items: string[]
  underlying: string
  note?: string
}

const CATEGORIES: Category[] = [
  {
    code: 'ERP',
    color: 'bg-blue-600',
    title: 'ERP / Core finance',
    subtitle: 'Application adapter layer',
    items: ['SAP S/4HANA', 'SAP Business One', 'Oracle NetSuite', 'MS Dynamics 365 BC/F&O', 'Sage X3 / 300 / Evolution', 'Syspro', 'Embrace', 'Infor', 'Epicor', 'Acumatica', 'Odoo', 'Custom ERP'],
    underlying: 'SAP HANA, SQL Server, Oracle, PostgreSQL',
    note: 'SAP guidance: raw application tables (MARA, VBAK, BKPF, BSEG, etc.) are discoverable once connected, but their names alone never justify a high-confidence canonical mapping — an authorized reviewer still has to approve every mapping before it becomes executable, same as any other source.',
  },
  {
    code: 'HR',
    color: 'bg-teal-600',
    title: 'Payroll & HR (HRIS)',
    subtitle: 'Joiner/mover/leaver source of truth',
    items: ['Sage VIP Payroll', 'Sage 300 People', 'PaySpace', 'SAP SuccessFactors', 'Oracle HCM Cloud', 'PSIber / UNIQ', 'PERSAL (provincial gov)', 'Custom HRIS'],
    underlying: 'SQL Server, PostgreSQL, SAP HANA',
  },
  {
    code: 'CRM',
    color: 'bg-purple-600',
    title: 'CRM',
    subtitle: 'Customer & revenue data',
    items: ['Salesforce', 'MS Dynamics 365 CRM', 'HubSpot', 'Zoho CRM'],
    underlying: 'Access via vendor API / OAuth connector, not direct DB',
    note: 'HubSpot has its own OAuth connector. Salesforce, Zoho CRM and Dynamics 365 CRM are ready-made templates on the REST API connection (see Data Sources → Files, SFTP or an API). † The templates follow each vendor\'s public API documentation and were tested against servers that answer in the documented shapes; they have not been run against a live tenant of that vendor.',
  },
  {
    code: 'BNK',
    color: 'bg-amber-600',
    title: 'Banking, treasury & payments',
    subtitle: 'Reconciliation & transaction evidence',
    items: ['BankservAfrica', 'SWIFT / Temenos T24', 'Corporate banking gateways', 'PayFast', 'Peach Payments', 'Ozow', 'Adumo'],
    underlying: 'Access via secure file feed or bank API, read-only',
    note: 'No vendor-specific connector for these. Where a bank or provider offers a file feed or a REST/SOAP API, connect it through the SFTP file-feed or API connection under Data Sources; the connector for that specific provider is not built.',
  },
  {
    code: 'PRC',
    color: 'bg-sky-600',
    title: 'Procurement & supply chain',
    subtitle: 'Segregation of duties, PO controls',
    items: ['SAP Ariba', 'Coupa', 'JAGGAER', 'National Treasury CSD / eTender'],
    underlying: 'SAP HANA, SQL Server, gov-hosted',
  },
  {
    code: 'POS',
    color: 'bg-rose-600',
    title: 'Point of sale / retail',
    subtitle: 'Revenue & inventory controls',
    items: ['Sage Pastel / Retail', 'MS Dynamics 365 Commerce', 'Micros / Aztec'],
    underlying: 'SQL Server, Actian/Pervasive (legacy)',
  },
  {
    code: 'DOC',
    color: 'bg-green-700',
    title: 'Document / records management',
    subtitle: 'Retention & evidence sourcing',
    items: ['SharePoint / M365', 'OpenText', 'Docuware'],
    underlying: 'SQL Server, Azure SQL, Oracle',
  },
  {
    code: 'BI',
    color: 'bg-violet-600',
    title: 'BI / analytics',
    subtitle: 'Cross-checking reported figures',
    items: ['Power BI', 'Tableau', 'QlikView / QlikSense', 'SAP BusinessObjects / BW'],
    underlying: 'Connector-based — resolves to the same underlying DB layer',
  },
  {
    code: 'DB',
    color: 'bg-slate-700',
    title: 'Direct database & API layer',
    subtitle: 'The layer every application above resolves to',
    items: ['Microsoft SQL Server', 'PostgreSQL', 'MySQL / MariaDB', 'Oracle Database', 'SAP HANA', 'Supabase', 'MongoDB', 'Snowflake', 'REST / SOAP APIs', 'CSV / Excel / SFTP files'],
    underlying: '',
  },
]

// The single source of truth for "is this connector actually built" — the
// catalogue below reads from this and nothing else, so shipping a new
// connector never means hunting down scattered per-card edits. A status
// only ever flips to 'live' once its end-to-end flow (connect -> discover
// -> mapping -> execution) is actually implemented and tested, never just
// because a driver package landed in requirements.txt.
type ConnectorStatus = 'live' | 'planned'
type ConnectorKind = 'database' | 'api_oauth' | 'api' | 'files'

// `caveat`: what has NOT been verified for a connector that is otherwise built and tested. It is shown on the chip
// (†), because "live" here means the flow works, not that it has been run against every vendor's real system.
const CONNECTOR_REGISTRY: Record<string, { status: ConnectorStatus; type: ConnectorKind; caveat?: string }> = {
  'Microsoft SQL Server': { status: 'live', type: 'database' },
  'PostgreSQL': { status: 'live', type: 'database' },
  'MySQL / MariaDB': { status: 'live', type: 'database' },
  'Supabase': { status: 'live', type: 'database' },
  'Oracle Database': { status: 'live', type: 'database' },
  'SAP HANA': { status: 'live', type: 'database' },
  'HubSpot': { status: 'live', type: 'api_oauth' },
  'Snowflake': { status: 'live', type: 'database' },
  'MongoDB': { status: 'live', type: 'database' },
  'REST / SOAP APIs': { status: 'live', type: 'api' },
  'CSV / Excel / SFTP files': { status: 'live', type: 'files' },
  'Salesforce': { status: 'live', type: 'api', caveat: 'Template on the REST connection, tested against a mock server that answers the way Salesforce documents; not yet run against a live Salesforce org.' },
  'Zoho CRM': { status: 'live', type: 'api', caveat: 'Template on the REST connection, tested against a mock server that answers the way Zoho documents; not yet run against a live Zoho account.' },
  'MS Dynamics 365 CRM': { status: 'live', type: 'api', caveat: 'Template on the REST connection, tested against a mock server that answers the way Dataverse documents; not yet run against a live Dynamics environment.' },
}

const KIND_LABEL: Record<ConnectorKind, string> = { database: 'DB', api_oauth: 'API/OAuth', api: 'API', files: 'Files' }

export function ConnectorCataloguePage() {
  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Connector Catalogue</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Every heterogeneous system MT AUDIT is designed to onboard, grouped by category. An application connects
        through the database it actually runs on — a new application on an already-supported database needs no new
        integration work at all.
      </p>

      <div className="mt-4 rounded-lg border border-accent-soft bg-accent-soft/40 p-4 text-sm">
        <div className="font-semibold text-accent-ink">What's actually live today</div>
        <p className="mt-1 text-ink">
          <span className="font-medium">PostgreSQL, MySQL, Microsoft SQL Server, Oracle Database, SAP HANA, Snowflake
          and MongoDB</span> work end to end through the database layer: connect directly to a database that is reachable
          from the internet, or through the <span className="font-medium">Gateway</span> (version 0.5.0 and later) for
          anything on a private network. Real connection testing, schema discovery, mapping and execution — not a mockup.
          <span className="font-medium"> Files</span> (CSV and Excel you upload, or the newest file matching a path on an
          <span className="font-medium"> SFTP</span> server) and <span className="font-medium">REST and SOAP APIs</span> are
          connections too: each file, sheet or API endpoint becomes a table that is discovered, mapped and tested exactly
          like a database table. <span className="font-medium">HubSpot</span> has its own OAuth connector.
        </p>
        <p className="mt-2 text-ink">
          <span className="font-medium">What has not been proven against a real system:</span> Oracle, SAP HANA and Snowflake
          through the Gateway are verified by their drivers' documented behaviour and by tests that need no server (no Oracle,
          HANA or Snowflake server was available); Salesforce, Zoho CRM and Dynamics 365 CRM are templates tested against
          servers that answer in each vendor's documented shape, not against a live tenant; and the Gateway executable
          has been built and inspected but not run on a real Windows client machine. The first connection to each is the
          remaining test — chips marked † carry that caveat.
        </p>
        <p className="mt-2 text-ink">
          Continuous monitoring works for both connection modes: a Gateway-relay connection runs its due tests on the
          client's own network on the Gateway's schedule, and a <span className="font-medium">direct</span> connection is
          picked up by the platform's own scheduler (polled every 30 seconds). Mapping, approval and control
          activation are identical either way.
        </p>
        <p className="mt-2 text-ink-soft">
          Set up a real connection from{' '}
          <Link to="/data-sources" className="font-medium text-accent-ink hover:underline">
            Data Sources
          </Link>
          .
        </p>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {CATEGORIES.map((cat) => (
          <div key={cat.code} className="rounded-lg border border-line bg-surface p-4">
            <div className="flex items-center gap-2">
              <span className={`flex h-7 w-7 items-center justify-center rounded-md text-xs font-bold text-white ${cat.color}`}>
                {cat.code}
              </span>
              <div>
                <div className="text-sm font-semibold text-ink">{cat.title}</div>
                <div className="text-xs text-ink-soft">{cat.subtitle}</div>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {cat.items.map((item) => {
                const entry = CONNECTOR_REGISTRY[item]
                const isLive = entry?.status === 'live'
                return (
                  <span
                    key={item}
                    className={`rounded-full px-2 py-0.5 text-xs ${isLive ? 'bg-accent-soft font-medium text-accent-ink' : 'bg-bg text-ink-soft'}`}
                    title={isLive ? (entry.caveat ?? `Live via the ${KIND_LABEL[entry.type]} layer`) : undefined}
                  >
                    {item}
                    {isLive ? ` ✓ ${KIND_LABEL[entry.type]}${entry.caveat ? ' †' : ''}` : ''}
                  </span>
                )
              })}
            </div>
            {cat.underlying && <p className="mt-3 text-xs text-ink-soft">Underlying databases: {cat.underlying}</p>}
            {cat.note && <p className="mt-1 text-xs text-amber-700">{cat.note}</p>}
          </div>
        ))}
      </div>

      <div className="mt-4 rounded-lg border border-line bg-ink p-5 text-sm text-white">
        <div className="font-semibold">New application? New database? Same framework.</div>
        <p className="mt-1 text-white/80">
          Any application not yet in the catalogue can still connect immediately if it sits on a supported database —
          the database layer handles connectivity, and only field-level mapping needs configuring per application.
          Adding a brand-new database engine (the way MongoDB and Snowflake just were) means adding one new driver to
          the direct-connection layer, and — separately — a new Gateway connector class if private-network relay
          access matters for it too. Adding a new vendor-API/OAuth connector (Salesforce, SAP SuccessFactors — the way
          HubSpot proved out) means implementing that vendor's own auth and discovery calls against the same
          api_connectors pattern — neither path means rebuilding the platform.
        </p>
      </div>
    </div>
  )
}
