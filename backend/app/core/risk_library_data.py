"""
Pre-built risk library, seeded automatically the same way the control
library is (see control_library_data.py). Two kinds of entries:

  - Generic (industry=None): one risk per control domain, so every
    organization gets a baseline risk register that already lines up with
    the controls that mitigate it — no blank Risks page to fill in by hand.
  - Industry-specific: extra risks that only make sense for a given
    industry (e.g. "Money laundering" for Banking, "Patient data breach"
    for Healthcare). An organization gets these when it selects that
    industry on creation.

Each entry: (industry_name | None, category_name, risk_name, risk_description, default_inherent_risk_rating)
`category_name` intentionally reuses the same 20 domain names as
control_library_data.py for the generic risks, so a risk and the controls
that mitigate it sit under one shared label throughout the platform.
"""

INDUSTRIES: list[str] = [
    "Banking & Financial Services",
    "Insurance",
    "Mining & Resources",
    "Manufacturing",
    "Retail & Wholesale Trade",
    "Healthcare & Pharmaceuticals",
    "Education",
    "Government & Public Sector",
    "Telecommunications",
    "Information Technology & Software",
    "Energy & Utilities",
    "Construction & Engineering",
    "Transportation & Logistics",
    "Agriculture & Agribusiness",
    "Real Estate & Property",
    "Hospitality & Tourism",
    "Media & Entertainment",
    "Professional & Business Services",
    "Non-Profit / NGO",
    "Automotive",
]

INDUSTRY_RISK_CATEGORY = "Industry & Regulatory Risk"

# Generic risks — apply to every organization regardless of industry.
GENERIC_RISK_LIBRARY: list[tuple[str, str, str, str]] = [
    ("User Access Management", "Unauthorized or excessive system access", "Users retain or are granted access beyond what their role requires, including after termination.", "high"),
    ("Segregation of Duties", "Conflicting duties enable fraud or error", "One person can both initiate and approve the same transaction (e.g. create a supplier and pay it).", "high"),
    ("Procure-to-Pay", "Fraudulent, duplicate or unauthorized payments to suppliers", "Payments are made without proper approval, matching, or to fictitious/duplicate suppliers.", "high"),
    ("Financial Transactions / General Ledger", "Misstated financial records", "Unauthorized or erroneous journal entries misstate the financial position.", "high"),
    ("Payroll", "Payroll fraud or ghost employees", "Salaries are paid to terminated, fictitious, or unauthorized employees, or changed without approval.", "high"),
    ("Master Data Management", "Fraudulent or duplicate master records", "Duplicate or fraudulently created supplier/customer records enable diverted payments.", "medium"),
    ("Order-to-Cash", "Revenue leakage", "Unapproved orders, excessive discounts, or unallocated receipts understate revenue collected.", "medium"),
    ("Change Management", "Unauthorized or untested changes disrupt production", "Changes reach production without approval or testing, causing outages or errors.", "high"),
    ("IT Operations", "Critical job or process failures go undetected", "Scheduled jobs fail or run late without triggering investigation or remediation.", "medium"),
    ("Backup & Recovery", "Inability to recover systems or data after a disruption", "Backups are missed, fail, or have never been tested for restore.", "high"),
    ("IT Asset Management", "Unaccounted or unsupported IT assets", "Assets are missing from the register, unassigned after staff leave, or run unsupported software.", "medium"),
    ("Data Privacy", "Unauthorized access to personal information", "Personal information is accessed, retained, or exposed beyond policy.", "high"),
    ("Encryption Controls", "Sensitive data exposed through weak or missing encryption", "Data at rest or in transit is inadequately protected.", "high"),
    ("Certificate Management", "Service disruption from expired certificates", "An expired or invalid certificate causes an outage or enables spoofing.", "medium"),
    ("API Controls", "Unauthorized or insecure API access", "APIs expose data or functionality without adequate authentication or encryption.", "high"),
    ("Vulnerability Management", "Unremediated vulnerabilities are exploited", "Known vulnerabilities remain open past an acceptable SLA.", "critical"),
    ("Network Controls", "Unauthorized network access", "Overly permissive firewall rules or unapproved connections expose the network.", "high"),
    ("Web Filtering / Content Security", "Malware or phishing via uncontrolled web access", "Endpoints without effective web filtering are exposed to malicious sites.", "medium"),
    ("Patch Management", "Unpatched or unsupported systems are exploited", "Critical patches are not applied within SLA, or systems run past end-of-life.", "high"),
    ("Remote Access", "Compromised or unauthorized remote access", "Remote access lacks MFA, authorization, or is not revoked when no longer needed.", "high"),
]

# Industry-specific risks, two per industry.
INDUSTRY_RISK_LIBRARY: list[tuple[str, str, str, str]] = [
    ("Banking & Financial Services", "Money laundering / inadequate AML-KYC controls", "Customer due diligence or transaction monitoring gaps allow illicit funds to move undetected.", "critical"),
    ("Banking & Financial Services", "Regulatory capital or liquidity breach", "The institution falls below required capital or liquidity ratios.", "high"),
    ("Insurance", "Fraudulent or inflated claims", "Claims are paid without adequate validation against policy terms and loss evidence.", "high"),
    ("Insurance", "Underwriting risk mispricing", "Policies are priced without adequate risk assessment, eroding profitability.", "medium"),
    ("Mining & Resources", "Environmental compliance breach", "Operations breach environmental permits or regulations, risking fines or shutdown.", "high"),
    ("Mining & Resources", "Mine health & safety incident", "Inadequate safety controls lead to injury or fatality on site.", "critical"),
    ("Manufacturing", "Supply chain disruption", "Reliance on single-source suppliers halts production when disrupted.", "high"),
    ("Manufacturing", "Workplace health & safety incident", "Inadequate machinery or process safety controls cause injury.", "high"),
    ("Retail & Wholesale Trade", "Inventory shrinkage or theft", "Stock losses go undetected due to weak inventory controls.", "medium"),
    ("Retail & Wholesale Trade", "Point-of-sale fraud", "Till/POS manipulation or refund fraud goes undetected.", "medium"),
    ("Healthcare & Pharmaceuticals", "Patient data privacy breach", "Protected health information is accessed or disclosed without authorization.", "critical"),
    ("Healthcare & Pharmaceuticals", "Medical billing or claims fraud", "Services are billed that were not rendered, or coded incorrectly.", "high"),
    ("Education", "Student data privacy breach", "Student records are accessed or disclosed without authorization.", "high"),
    ("Education", "Misuse of donor or tuition funds", "Funds are used outside of their designated purpose without oversight.", "medium"),
    ("Government & Public Sector", "Procurement or tender irregularities", "Tenders are awarded without fair, transparent process, enabling favoritism or fraud.", "high"),
    ("Government & Public Sector", "Misuse of public funds", "Public funds are spent without proper authorization or oversight.", "critical"),
    ("Telecommunications", "Network outage or service disruption", "Core network failures interrupt service to subscribers.", "high"),
    ("Telecommunications", "Subscriber data breach", "Subscriber personal or billing data is exposed.", "high"),
    ("Information Technology & Software", "Source code or intellectual property leakage", "Proprietary code or IP is exposed to unauthorized parties.", "high"),
    ("Information Technology & Software", "Software supply chain compromise", "A compromised dependency or build pipeline introduces malicious code.", "high"),
    ("Energy & Utilities", "Critical infrastructure disruption", "A failure or attack disrupts power, water, or utility service delivery.", "critical"),
    ("Energy & Utilities", "Environmental or regulatory non-compliance", "Operations breach environmental or utility regulations.", "high"),
    ("Construction & Engineering", "Project cost overrun or fraud", "Projects run over budget due to inadequate cost control or fraudulent billing.", "high"),
    ("Construction & Engineering", "Site health & safety incident", "Inadequate site safety controls cause injury.", "high"),
    ("Transportation & Logistics", "Cargo loss, theft or diversion", "Goods in transit are lost, stolen, or diverted without detection.", "medium"),
    ("Transportation & Logistics", "Fleet or driver compliance failure", "Vehicles or drivers operate without required licensing, maintenance, or hours compliance.", "medium"),
    ("Agriculture & Agribusiness", "Crop or livestock loss from inadequate controls", "Inadequate monitoring leads to preventable loss of crops or livestock.", "medium"),
    ("Agriculture & Agribusiness", "Food safety compliance breach", "Products fail to meet food safety standards, risking recall or harm.", "high"),
    ("Real Estate & Property", "Rental income leakage or fraud", "Rental collections are diverted or under-recorded.", "medium"),
    ("Real Estate & Property", "Property valuation misstatement", "Properties are valued incorrectly, misstating the balance sheet.", "medium"),
    ("Hospitality & Tourism", "Guest payment card data breach", "Payment card data handled by the property is exposed.", "high"),
    ("Hospitality & Tourism", "Revenue leakage", "Comps, no-shows, or cash handling are not properly controlled.", "medium"),
    ("Media & Entertainment", "Content piracy or IP infringement", "Content is distributed or used without proper rights or protection.", "medium"),
    ("Media & Entertainment", "Royalty or rights payment misstatement", "Royalties owed to rights holders are calculated or paid incorrectly.", "medium"),
    ("Professional & Business Services", "Client confidentiality breach", "Confidential client information is disclosed without authorization.", "high"),
    ("Professional & Business Services", "Time or billing fraud", "Hours or expenses are billed to clients without proper support.", "medium"),
    ("Non-Profit / NGO", "Donor fund misappropriation", "Donor funds are used outside their designated purpose.", "high"),
    ("Non-Profit / NGO", "Grant compliance breach", "Grant conditions or reporting requirements are not met.", "high"),
    ("Automotive", "Warranty claim fraud", "Warranty claims are submitted without valid supporting defects.", "medium"),
    ("Automotive", "Dealer or supplier rebate fraud", "Rebate or incentive programs are manipulated by dealers or suppliers.", "medium"),
]
