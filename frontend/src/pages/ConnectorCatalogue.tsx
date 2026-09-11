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
    note: 'HubSpot proves the API/OAuth connector model: a genuinely separate connector family from the database layer below, with its own token-refresh and revocation-detection lifecycle — see Data Sources to connect it. The rest of this category still needs their own vendor-specific OAuth integration built the same way.',
  },
  {
    code: 'BNK',
    color: 'bg-amber-600',
    title: 'Banking, treasury & payments',
    subtitle: 'Reconciliation & transaction evidence',
    items: ['BankservAfrica', 'SWIFT / Temenos T24', 'Corporate banking gateways', 'PayFast', 'Peach Payments', 'Ozow', 'Adumo'],
    underlying: 'Access via secure file feed or bank API, read-only',
    note: 'Not yet built — these need a file-feed or bank-API connector, not built yet.',
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
type ConnectorKind = 'database' | 'api_oauth'

const CONNECTOR_REGISTRY: Record<string, { status: ConnectorStatus; type: ConnectorKind }> = {
  'Microsoft SQL Server': { status: 'live', type: 'database' },
  'PostgreSQL': { status: 'live', type: 'database' },
  'MySQL / MariaDB': { status: 'live', type: 'database' },
  'Supabase': { status: 'live', type: 'database' },
  'Oracle Database': { status: 'live', type: 'database' },
  'SAP HANA': { status: 'live', type: 'database' },
  'HubSpot': { status: 'live', type: 'api_oauth' },
  'Snowflake': { status: 'live', type: 'database' },
  'MongoDB': { status: 'live', type: 'database' },
}

const KIND_LABEL: Record<ConnectorKind, string> = { database: 'DB', api_oauth: 'API/OAuth' }

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
          and MongoDB</span> are working end to end via the database layer — connect directly to a cloud-hosted
          database (all seven), or via the Gateway for anything on a private network (Postgres/MySQL/MSSQL only —
          Oracle, SAP HANA, Snowflake and MongoDB don't have a Gateway-relay connector yet, direct-cloud only) — real
          connection testing and schema discovery, not a mockup.
          <span className="font-medium"> HubSpot</span> is fully working end to end via a genuinely separate
          <span className="font-medium"> API/OAuth</span> connector — no SQL involved, its own token-refresh and
          revocation-detection lifecycle. Anything below marked <span className="font-medium">✓ DB</span> or <span className="font-medium">✓ API/OAuth</span> works
          today for exactly that reason: it sits on one of those. Everything else (every other CRM/ERP/banking vendor)
          is the target catalogue this platform is built to extend to next — not yet connectable.
        </p>
        <p className="mt-2 text-amber-800">
          Known gap, applies to every connector above: a <span className="font-medium">direct</span> connection (and
          HubSpot) can be tested and schema-discovered today, but there's no scheduler yet that picks it up for
          <span className="font-medium"> continuous</span> monitoring test execution — only a Gateway-relay connection
          runs on a recurring schedule right now. Mapping/approval/control-activation all work the same regardless of
          connection mode; only the "run this automatically, repeatedly" step is Gateway-only so far.
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
                    title={isLive ? `Live via the ${KIND_LABEL[entry.type]} layer` : undefined}
                  >
                    {item}
                    {isLive ? ` ✓ ${KIND_LABEL[entry.type]}` : ''}
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
