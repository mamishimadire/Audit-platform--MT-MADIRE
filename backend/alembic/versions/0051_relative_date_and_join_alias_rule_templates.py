"""Extend rule-template coverage, part 2: IT Operations, Backup & Recovery,
IT Asset Management, Data Privacy, Encryption Controls, Certificate
Management, API Controls, Vulnerability Management, Network Controls, Web
Filtering / Content Security, Patch Management, and Remote Access — the
second half of the 20-domain library, continuing 0050 (User Access
Management through Change Management).

Same validation discipline as 0050 and every prior rule-template migration:
every rule_definition here was schema-validated against app.schemas.
test_rule's Pydantic models AND dry-run against BOTH engines (app.services.
rule_evaluation and gateway.gateway.rule_engine) with synthetic good/bad
rows, confirming each produces exactly the expected exception set in both.
See 0050's docstring for the shared context on the 4 new capabilities
(RelativeDate, field_comparison, primary_condition, secondary_join_field)
and the techniques reused below (pass-through conditions, self-joins,
reused cross-object rules).

=== New technique: secondary_join_field for a real value correspondence
    (not just a name difference) ===
secondary_join_field only helps when the two sides hold the SAME kind of
value under different field names — e.g. asset_register.assigned_to and
employee.employee_id both hold literal employee-id strings (AS-002/AS-003
below), or remote_access_tools.tool_name and approved_software.
software_name both hold literal software/tool-name strings (RA-009 below).
It does NOT help when the two objects simply have no field that holds a
corresponding value at all (asset_register.asset_id vs system_inventory.
system_id are different ID *schemes* with nothing that maps one to the
other in the canonical model) — that is a structural gap no amount of
field-name aliasing fixes, and several controls below that were catalogued
under "no shared join-field name" turned out to be this deeper problem on
closer inspection. Each is called out individually below, per this
migration's mandate not to assume the prior one-line category guess.

=== The audit_logs polymorphic join, resolved (not assumed) ===
The post-0049 gap catalogue asked specifically whether audit_logs's
entity_type/entity_id structure "resolves cleanly with a plain asymmetric
join" now that secondary_join_field exists, or whether the polymorphic
part is a separate, still-blocking problem. Re-checking it directly: it is
still blocking. secondary_join_field lets entity_id be compared against a
differently-named primary-side key, but audit_logs is a single table
shared by every entity type in the system — entity_id only means
"payroll_changes.change_id" (say) when entity_type == "payroll_changes",
and nothing in missing_match or cross_match_condition filters the
SECONDARY object by a condition before joining (only MissingMatchRule's
primary_condition exists, and it only ever applies to the primary side).
DP-002, MD-005 (0050), and PY-010 (0050) all share exactly this gap; DP-002
is confirmed still blocked below for this specific, now-precise reason.

=== Corrections to the post-0049 catalogue found by re-deriving each
    control (rather than trusting its one-line category) ===
  - AS-001 and NW-004 were catalogued as "no shared join-field name" (implying
    secondary_join_field would fix them) but are actually the deeper "no
    value correspondence at all" problem described above — corrected below.
  - VM-003 was catalogued under "relative-date" but its real join path
    (assets -> software_inventory -> support_matrix) has no linking field
    at any hop even before considering recency — corrected below.
  - PM-001 and VM-002 were catalogued under "relative-date" but each also
    needs a dynamic per-severity threshold from sla_rules (remediate/patch_
    within_days varies by severity, and only "critical" has a defined SLA
    row) — RelativeDate's offset is a fixed literal baked into the rule at
    template-authoring time, it cannot resolve a per-row value from another
    table the way a true dynamic lookup would. Baking today's single "7" or
    "14" day SLA value into the template would silently go stale the
    moment a client edits sla_rules or adds a new severity tier — the same
    discipline call 0049 made rejecting RA-010's hardcoded max_idle_minutes.
    Both remain blocked, now for a precisely identified compound reason.
  - BK-002, BK-004, and OP-007 were catalogued under "relative-date" but
    each needs a per-row dynamic offset sourced from data (backup_
    schedules.frequency, backup_retention_rules.retention_days, log_
    retention_rules.retention_days respectively) rather than a fixed
    number of days — the same "would go stale" problem as PM-001/VM-002
    above, just with a schedule string or retention_days field standing in
    for the missing SLA lookup. All three remain blocked.
  - OP-003 was catalogued under "relative-date" too. Its seed fixture's
    one concrete violation (JOB003, a weekly job with zero job_executions
    rows ever) would incidentally pass an unfiltered missing_match with no
    date logic at all — but that tests a materially narrower thing
    ("has this job EVER run") than the control's actual wording ("must run
    ACCORDING TO SCHEDULE", i.e. ongoing cadence adherence), and scheduled_
    jobs.schedule is a free-text string ("daily 02:00", "monthly",
    "weekly") with no canonical numeric-window mapping. Templating the
    existence-only version to make the fixture pass, while quietly not
    testing what the control actually asks for, was rejected — same
    discipline as GL-004's rejected shortcut in 0050/0046. Still blocked.
  - DP-004 was catalogued under "relative-date" but is compounded exactly
    like 0048 already found: data_records has no "dataset" field to even
    join against retention_rules, on top of retention_days being a
    dynamic per-dataset lookup, not a fixed offset. Still blocked, for
    both reasons together.

=== Templated in this migration ===

IT Operations:
  - OP-002 "Failed jobs must be investigated": missing_match,
    job_executions -> incident_records on job_id, primary_condition
    status == "failed". incident_records has no status-like field of its
    own (only incident_id/job_id/opened_at), so unlike AC-001 (0050),
    existence of ANY matching row genuinely does mean "was investigated" —
    no secondary-side filter is needed here.

Backup & Recovery:
  - BK-001 "Critical systems must be backed up": missing_match, system ->
    backup_jobs on system_id, primary_condition criticality == "critical".

IT Asset Management:
  - AS-002 "Assigned assets must belong to valid employees": missing_match,
    asset_register -> employee, join_field "assigned_to", secondary_
    join_field "employee_id" (asset_register.assigned_to holds a literal
    employee_id value under a different field name), primary_condition
    assigned_to is_not_null (unassigned assets are out of scope).
  - AS-003 "Terminated employees must return assets": cross_match_
    condition, employee -> asset_register, join_field "employee_id",
    secondary_join_field "assigned_to", condition_primary
    employment_status != "active", condition_secondary assigned_to
    is_not_null.
  - AS-005 "Unsupported systems should be identified": cross_match_
    condition, system_inventory -> support_matrix, join_field "os" (a
    genuinely shared field name — both objects already use "os"),
    condition_primary is_not_null pass-through, condition_secondary
    supported_until <= now (RelativeDate). Note: the seed fixture's own
    system_inventory row for SYS003 stores os as "Ubuntu" while its
    support_matrix counterpart stores "Ubuntu 18.04" — those two strings
    do not literally match, so this rule (correctly written against the
    canonical field) will not reproduce that one fixture's exact violation
    until the demo data's os values are made consistent between the two
    tables. That is a demo-fixture data-quality quirk, not a rule defect —
    confirmed by this migration's own synthetic dry-run, which uses
    matching os strings on both sides and gets the expected result.

Data Privacy:
  - DP-005 "Data deletion requests should be processed": missing_match,
    privacy_requests -> deletion_records on request_id, primary_condition
    type == "deletion".

Encryption Controls:
  - EN-001 "Sensitive data must be encrypted": missing_match, data_assets
    -> encryption_config on asset_name, primary_condition sensitivity ==
    "high". Scoped to "no encryption_config row on file at all" — the
    specific gap 0047 named (an unfiltered version would demand a config
    row for every asset, sensitive or not). A sensitive asset that DOES
    have an encryption_config row but with encrypted == False is a
    different, complementary case this control's single rule does not
    cover (no secondary-side filter exists, same limitation noted for
    AC-001/OP-002 above) — there is no sibling control in this domain
    (unlike WF-001/WF-010's deliberate split) to carry that second half,
    so it remains an acknowledged, undupllicated gap.
  - EN-003 "TLS certificates must be valid": reuses CERT-001's shape
    exactly (see below) — 0048 already identified this as "the same gap
    as CERT-001, over the same underlying certificates table."

Certificate Management:
  - CERT-001 "Certificates must not expire unexpectedly": threshold,
    certificates.expires_at <= 30 days from now (RelativeDate). An
    already-expired certificate also satisfies this (its expiry is
    trivially "within" any future threshold), which is correct: an
    expired cert is the most urgent case of "expiring within threshold,"
    not an exception to it.
  - CERT-002 "Expired certificates must be identified": threshold,
    certificates.expires_at < now (RelativeDate, relative_days 0).

API Controls:
  - API-005 "API keys should expire": threshold, api_keys.expires_at <
    now (RelativeDate). Scoped to "expired," the concrete half of this
    control's "expired/old" wording — "old but not yet expired" would be
    a separate, less precise test (a key's mere age says nothing about
    whether it's actually still valid) and isn't attempted here.

Remote Access:
  - RA-008 "Third-party/vendor remote access must be time-bound and
    logged": threshold, vendor_access_grants.expires_at < now
    (RelativeDate). This is the "time-bound" half only — 0049 was explicit
    that this half "needs a relative-date comparison regardless" of the
    join gap, so RelativeDate resolves it independently. The "logged" half
    (matching to remote_access_logs) remains unaddressed: vendor_access_
    grants is keyed by `vendor`, remote_access_logs by user_id/session_id,
    and there is no field on either side holding a value that corresponds
    to the other — not a naming mismatch secondary_join_field could fix,
    a genuine absence of any linking value. Honestly partial, same
    precedent as EN-002 (0048) and EN-001/AC-006 above.
  - RA-009 "Remote access tools must be from an approved catalogue":
    missing_match, remote_access_tools -> approved_software, join_field
    "tool_name", secondary_join_field "software_name" (both hold literal
    software/tool-name strings — a real value correspondence, unlike
    AS-001/NW-004 below).

Web Filtering / Content Security:
  - WF-007 "Newly categorised/emerging threat domains must be updated
    within SLA": threshold, web_filter_config.updated_at <= 90 days ago
    (RelativeDate). This is the recency half only — 0049 named "a relative-
    date/version-recency comparison" as necessary regardless of the join
    gap, and that join gap (web_filter_config.config_id vs patch_releases.
    patch_id, "unrelated domains" per 0049) is real and not addressed
    here: there is no field on either side holding a value that
    corresponds to the other, so the actual "compare against the vendor's
    release" half of this control is not attempted. Honestly partial, same
    precedent as EN-002 (0048), RA-008, and EN-001 above.

Patch Management:
  - PM-003 "End-of-life/unsupported systems must be flagged": reuses
    AS-005's exact shape (system_inventory <-> support_matrix on "os",
    supported_until <= now) — 0047 already identified this as the same
    condition over the same table pair.

=== Deliberately NOT templated in this migration, with the specific
    remaining reason ===

IT Operations (OP-003, OP-004, OP-005, OP-007):
  - OP-003: still blocked — see the correction note above (schedule is a
    free-text cadence string with no numeric-window mapping; an existence-
    only version would test something materially narrower than the
    control asks for).
  - OP-004: unchanged from 0044 — job_executions.duration_seconds is real
    and usable, but no canonical field defines a per-job or global limit.
  - OP-005: unchanged — incidents has no "system" field and system_logs
    has no "incident_id" field; no value correspondence exists at all.
  - OP-007: still blocked — see the correction note above (log_retention_
    rules.retention_days is a per-system dynamic lookup, not a fixed
    offset RelativeDate can express).

Backup & Recovery (BK-002, BK-004):
  - BK-002, BK-004: still blocked — see the correction note above
    (backup_schedules.frequency and backup_retention_rules.retention_days
    are both per-row dynamic values, not fixed offsets).

IT Asset Management (AS-001):
  - AS-001 "IT assets must be recorded": still blocked — see the
    correction note above. asset_register.asset_id and system_inventory.
    system_id are different ID schemes (AST-prefixed vs SYS-prefixed in
    the seed data) with no field on either side holding the other's value;
    secondary_join_field aliases a field NAME, it cannot manufacture a
    VALUE correspondence that doesn't exist in the data model.

Data Privacy (DP-002, DP-004):
  - DP-002: still blocked — the audit_logs polymorphic-join gap described
    above.
  - DP-004: still blocked — compounded gap described above (no "dataset"
    field on data_records, plus a dynamic per-dataset retention_days
    lookup).

Encryption Controls (EN-004):
  - EN-004 "Weak encryption protocols must be identified": still blocked
    — system_configurations (system_id, tls_version) and security_
    baselines (control, required_value) share no value correspondence at
    all; security_baselines is a small set of global named policy rows
    ("control" is a policy label like "min_tls_version"), not a per-system
    foreign key. Even with a join, tls_version ("TLS 1.2") vs
    required_value would be a version-string comparison, not a plain eq.

Vulnerability Management (VM-001, VM-002, VM-003):
  - VM-001: unchanged from 0044/0048 — describes a data-ingestion process,
    not a row-level condition.
  - VM-002: still blocked — see the correction note above (dynamic
    per-severity SLA lookup, same class of gap as PM-001 below).
  - VM-003: still blocked — see the correction note above (no join path
    exists between assets/software_inventory and support_matrix at any
    hop, a deeper problem than relative-date alone; corrects the original
    catalogue entry).

Network Controls (NW-003, NW-004):
  - NW-003: unchanged from 0049 — no canonical field defines what
    "excessively permissive" means; templating a hardcoded port number
    would invent an undefined client policy.
  - NW-004: still blocked — see the correction note above (network_
    devices and asset_register share no value correspondence, the same
    class of gap as AS-001, not a naming mismatch).

Web Filtering / Content Security (WF-002, WF-003, WF-004, WF-006, WF-009):
  - WF-002: unchanged from 0049 — category-to-control-name is a business
    mapping, not a literal shared value.
  - WF-003: unchanged — web_filter_logs and security_logs share no
    linking value at all.
  - WF-004: unchanged — needs a filtered-duplicate capability (group by
    (user_id, url) only among action == "blocked" rows); DuplicateRule
    still has no group-level pre-filter.
  - WF-006: unchanged — web_filter_status is asset-keyed, remote_access_
    logs is user/session-keyed; no shared value.
  - WF-009: unchanged — web_filter_config and certificates share no value
    correspondence (config_id vs cert_id, and no field marks which
    certificate belongs to which TLS-inspection config).

Patch Management (PM-001, PM-002, PM-008, PM-009, PM-010):
  - PM-001: still blocked — see the correction note above (dynamic
    per-severity SLA lookup, same class of gap as VM-002).
  - PM-002: unchanged from 0049 — patch_inventory has no unique per-row
    key (its natural key is the asset_id+patch_id pair), so a safe
    self-join on its `installed` boolean isn't possible, and ThresholdRule
    still cannot take a boolean value directly.
  - PM-008: unchanged — same no-unique-row-id gap as PM-002, compounded by
    a 3-way per-asset-per-required-patch reconciliation.
  - PM-009: still blocked — software_inventory and patch_releases share no
    value correspondence (nothing connects an installed software title to
    a specific patch release), not a naming-only mismatch.
  - PM-010: still blocked — patch_exceptions has no exception_id field (or
    any field) that corresponds to exception_approvals.exception_id;
    secondary_join_field cannot alias a value that isn't recorded anywhere
    on patch_exceptions.

Remote Access (RA-002, RA-003, RA-004, RA-006, RA-007, RA-010, RA-011,
RA-012):
  - RA-002 "Remote access must be explicitly authorised": re-checked
    specifically (not previously re-stated in the post-0049 catalogue's
    summary list) — still blocked. remote_access_grants has no per-grant
    unique id (only user_id/granted_at/approved, and a user can plausibly
    be granted, revoked, and re-granted over time), so missing_match
    against access_requests would still only be the same coarse, non-
    grant-specific user_id match 0049 already rejected for AC-007/RA-001 —
    primary_condition filters ROWS, it doesn't fix an imprecise JOIN KEY.
  - RA-003, RA-004, RA-007: unchanged from 0049 — each needs a 3-way chain
    (vpn_accounts/remote_access_logs -> user -> employee, or -> user_roles
    -> privileged_access_approvals).
  - RA-006: unchanged — access_policies.allowed_countries is list-typed;
    no list-membership operator exists, and this is also a field-to-field
    (not literal) comparison.
  - RA-010, RA-012: still blocked — remote_access_logs/vpn_config and
    session_policies/network_policies are singleton config-style tables
    with no field on either side holding a corresponding value at all (no
    session_id/user_id on session_policies, no config_id on remote_access_
    logs) — field_comparison compares two fields on an already-JOINED row;
    there is no join to perform here in the first place, so it cannot
    apply. secondary_join_field has the same limit as with AS-001/NW-004
    above: it aliases a field name, it cannot manufacture a missing value
    correspondence.
  - RA-011 "Failed remote login attempts must be monitored": re-checked
    specifically — still blocked. Neither remote_access_logs nor
    login_history has any success/failure outcome field at all; a genuine
    missing-canonical-field gap, not previously re-stated in the post-0049
    catalogue's summary list.

Revision ID: 0051
Revises: 0050
Create Date: 2026-09-16
"""
import json

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "OP-002",
        "Failed job execution has no recorded incident",
        {
            "rule_type": "missing_match",
            "primary_object": "job_executions",
            "secondary_object": "incident_records",
            "join_field": "job_id",
            "primary_condition": {"field": "status", "operator": "eq", "value": "failed"},
        },
    ),
    (
        "BK-001",
        "Critical system has no backup job on file",
        {
            "rule_type": "missing_match",
            "primary_object": "system",
            "secondary_object": "backup_jobs",
            "join_field": "system_id",
            "primary_condition": {"field": "criticality", "operator": "eq", "value": "critical"},
        },
    ),
    (
        "AS-002",
        "Assigned asset does not belong to a valid employee",
        {
            "rule_type": "missing_match",
            "primary_object": "asset_register",
            "secondary_object": "employee",
            "join_field": "assigned_to",
            "secondary_join_field": "employee_id",
            "primary_condition": {"field": "assigned_to", "operator": "is_not_null"},
        },
    ),
    (
        "AS-003",
        "Terminated employee still has an assigned asset",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "employee",
            "secondary_object": "asset_register",
            "join_field": "employee_id",
            "secondary_join_field": "assigned_to",
            "condition_primary": {"field": "employment_status", "operator": "ne", "value": "active"},
            "condition_secondary": {"field": "assigned_to", "operator": "is_not_null"},
        },
    ),
    (
        "AS-005",
        "System is running an unsupported (end-of-life) OS version",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "system_inventory",
            "secondary_object": "support_matrix",
            "join_field": "os",
            "condition_primary": {"field": "system_id", "operator": "is_not_null"},
            "condition_secondary": {"field": "supported_until", "operator": "lte", "value": {"kind": "relative_date", "relative_days": 0}},
        },
    ),
    (
        "DP-005",
        "Deletion request has no recorded deletion",
        {
            "rule_type": "missing_match",
            "primary_object": "privacy_requests",
            "secondary_object": "deletion_records",
            "join_field": "request_id",
            "primary_condition": {"field": "type", "operator": "eq", "value": "deletion"},
        },
    ),
    (
        "EN-001",
        "Sensitive data asset has no encryption configuration on file",
        {
            "rule_type": "missing_match",
            "primary_object": "data_assets",
            "secondary_object": "encryption_config",
            "join_field": "asset_name",
            "primary_condition": {"field": "sensitivity", "operator": "eq", "value": "high"},
        },
    ),
    (
        "EN-003",
        "Certificate is expiring within 30 days",
        {"rule_type": "threshold", "object": "certificates", "field": "expires_at", "operator": "lte", "value": {"kind": "relative_date", "relative_days": 30}},
    ),
    (
        "CERT-001",
        "Certificate is expiring within 30 days",
        {"rule_type": "threshold", "object": "certificates", "field": "expires_at", "operator": "lte", "value": {"kind": "relative_date", "relative_days": 30}},
    ),
    (
        "CERT-002",
        "Certificate has expired",
        {"rule_type": "threshold", "object": "certificates", "field": "expires_at", "operator": "lt", "value": {"kind": "relative_date", "relative_days": 0}},
    ),
    (
        "API-005",
        "API key has expired",
        {"rule_type": "threshold", "object": "api_keys", "field": "expires_at", "operator": "lt", "value": {"kind": "relative_date", "relative_days": 0}},
    ),
    (
        "RA-008",
        "Vendor remote access grant has expired",
        {"rule_type": "threshold", "object": "vendor_access_grants", "field": "expires_at", "operator": "lt", "value": {"kind": "relative_date", "relative_days": 0}},
    ),
    (
        "RA-009",
        "Remote access tool is not on the approved software catalogue",
        {
            "rule_type": "missing_match",
            "primary_object": "remote_access_tools",
            "secondary_object": "approved_software",
            "join_field": "tool_name",
            "secondary_join_field": "software_name",
        },
    ),
    (
        "WF-007",
        "Web filter category database is stale (not updated in 90+ days)",
        {"rule_type": "threshold", "object": "web_filter_config", "field": "updated_at", "operator": "lte", "value": {"kind": "relative_date", "relative_days": -90}},
    ),
    (
        "PM-003",
        "System is running an unsupported (end-of-life) OS version",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "system_inventory",
            "secondary_object": "support_matrix",
            "join_field": "os",
            "condition_primary": {"field": "system_id", "operator": "is_not_null"},
            "condition_secondary": {"field": "supported_until", "operator": "lte", "value": {"kind": "relative_date", "relative_days": 0}},
        },
    ),
]


def upgrade() -> None:
    for control_code, rule_name, rule_definition in _TEMPLATES:
        rule_definition_json = json.dumps(rule_definition).replace("'", "''")
        rule_name_escaped = rule_name.replace("'", "''")
        op.execute(
            f"""
            INSERT INTO control_rule_templates (control_library_id, rule_name, rule_definition)
            SELECT control_library_id, '{rule_name_escaped}', '{rule_definition_json}'
            FROM control_library
            WHERE control_code = '{control_code}'
            ON CONFLICT (control_library_id) DO NOTHING
            """
        )


def downgrade() -> None:
    codes = ", ".join(f"'{code}'" for code, _, _ in _TEMPLATES)
    op.execute(
        f"""
        DELETE FROM control_rule_templates
        WHERE control_library_id IN (SELECT control_library_id FROM control_library WHERE control_code IN ({codes}))
        """
    )
