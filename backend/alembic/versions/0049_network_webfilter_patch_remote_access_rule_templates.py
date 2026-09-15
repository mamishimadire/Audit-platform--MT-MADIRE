"""Extend rule-template coverage: Network Controls, Web Filtering / Content
Security, Patch Management, and Remote Access — the last four domains,
completing the pass started in 0044 and continued in 0046-0048.

Same validation discipline throughout: every rule_definition here was
schema-validated against app.schemas.test_rule AND dry-run against a copy
of the Gateway rule engine with synthetic good/bad rows. See 0046's
docstring for the "boolean self-filter" and "reused cross-object rule"
techniques used again below.

A fifth gap category, mostly confined to this migration, worth naming on
its own: several tables here (patch_inventory, remote_access_grants) have
NO field that uniquely identifies one row — patch_inventory's natural key
is the (asset_id, patch_id) PAIR, and an asset can have many patch rows.
The "boolean self-filter" self-join technique (0046) depends on its
join_field being a genuine one-row-per-value key in that table — patch_
deployments.deployment_id and remote_access_logs.session_id are (one row
per deployment/session), but patch_inventory.asset_id is NOT (one row per
asset+patch combination). Self-joining patch_inventory on asset_id would
merge every patch row for an asset against every OTHER patch row for that
same asset, producing multiplied, spurious exception records instead of
one real finding per actual failed patch — a correctness bug, not a
cosmetic one. Controls that would otherwise be a one-line boolean check
(PM-002, PM-008) are skipped for exactly this reason, not because the
condition itself is hard to express.

=== Templated in this migration ===
Network Controls:
  - NW-001 "Unauthorised network connections should be identified":
    boolean self-filter, network_connections.approved == False,
    self-joined on connection_id (a genuine per-row key). Used instead of
    joining to the separate approved_connections table, which carries no
    other information beyond the id itself and adds nothing the record's
    own `approved` field doesn't already say more directly.
  - NW-002 "Firewall rules require approval": missing_match, firewall_rules
    -> firewall_approvals on rule_id.
Web Filtering / Content Security:
  - WF-001 "Web filtering must be active on all endpoints": boolean
    self-filter, web_filter_status.filtering_active == False, self-joined
    on asset_id (one status row per asset).
  - WF-005 "Filtering policy exceptions/overrides require approval":
    missing_match, web_filter_exceptions -> exception_approvals on
    exception_id.
  - WF-008 "Endpoints must not have unauthorised filtering bypass/proxy
    tools": same missing_match shape as the already-templated AS-004
    (software_inventory -> approved_software on software_name) — a
    different control code testing the identical real condition
    ("is this installed software on the approved list").
  - WF-010 "Filtering coverage must extend to all managed devices":
    missing_match, asset_register -> web_filter_status on asset_id. This
    is deliberately the COMPLEMENT of WF-001, not a duplicate of it: WF-001
    catches assets that HAVE a web_filter_status row with filtering_active
    == False; WF-010 catches assets with NO web_filter_status row at all
    (confirmed against the seed fixture — AST003/AST004 are in
    asset_register but have no web_filter_status row, a genuinely
    different exception than AST002's filtering_active == False). Together
    they give real, non-overlapping coverage of "coverage must extend to
    all managed devices."
Patch Management:
  - PM-004 "Patch deployment must be approved": boolean self-filter,
    patch_deployments.approved == False, self-joined on deployment_id (a
    genuine per-row key — unlike patch_inventory, one row per deployment).
  - PM-005 "Patch testing must occur before production deployment": boolean
    self-filter, patch_deployments.tested == False, self-joined on
    deployment_id. Note test_results (used elsewhere for CM-002) is keyed
    by change_id, not deployment_id, and patch_deployments has no
    change_id field — so the cross-table join isn't available here even in
    principle; patch_deployments' own `tested` field is the only usable
    signal, and it's a direct, reliable one.
  - PM-006 "Emergency patches require retrospective approval": a genuine
    2-condition self-join (not a single-field boolean filter) — patch_
    deployments.emergency == True AND patch_deployments.approved == False,
    both real fields on the same table, self-joined on deployment_id. Same
    shape as 0044's OP-006 (severity == critical AND status != resolved).
  - PM-007 "Failed patch deployments must be investigated": boolean
    self-filter, patch_incidents.resolved == False, self-joined on
    deployment_id (assumed one incident row per failed deployment,
    consistent with the seed data).
Remote Access:
  - RA-001 "Remote access must require MFA": boolean self-filter, remote_
    access_logs.mfa_used == False, self-joined on session_id (a genuine
    per-row key). Used instead of joining to mfa_config (user-level, not
    session-level — a coarser match, the same imprecision issue noted for
    AC-007 in 0046) since remote_access_logs already carries the
    session-specific signal directly.
  - RA-005 "Remote sessions must be logged": missing_match, remote_
    access_logs -> session_audit on session_id.

=== Deliberately NOT templated, with the specific gap ===

Network Controls (NW-003, NW-004):
  - NW-003 "Excessively permissive firewall rules should be identified":
    firewall_rules and security_baselines share no join_field, and the
    seeded "violation" (port 3389 open to 0.0.0.0/0) is a specific,
    invented business rule ("RDP shouldn't be open to the internet") that
    exists nowhere in canonical data — templating a hardcoded "port ==
    3389" check would be inventing an undefined policy, the same
    discipline call as OTC-008/OP-004's rejected magnitude thresholds.
  - NW-004 "Network devices must be inventoried": network_devices (device_
    id, hostname) and asset_register (asset_id, asset_name) share no
    common field name — a join-alias gap like AS-001/AS-002.

Web Filtering / Content Security (WF-002, WF-003, WF-004, WF-006, WF-007,
WF-009):
  - WF-002 "Malicious/blacklisted categories must be blocked by policy":
    web_filter_policies.category and security_baselines.control are a
    business-name mapping ("malware" category <-> "block_malware_category"
    control), not a literal shared field.
  - WF-003 "Blocked access attempts must be logged": web_filter_logs and
    security_logs share no linking id at all.
  - WF-004 "Repeated attempts to access malicious sites should be
    investigated": same filtered-duplicate gap as API-003 (0048) — grouping
    web_filter_logs by (user_id, url) without first filtering to action ==
    "blocked" would also flag repeated normal, allowed visits to the same
    URL as "malicious site" exceptions.
  - WF-006 "Filtering must apply consistently on and off network":
    web_filter_status is asset-keyed, remote_access_logs is user/session-
    keyed — no shared join_field between an asset-level and a user-level
    table.
  - WF-007 "Newly categorised/emerging threat domains must be updated
    within SLA": needs a relative-date/version-recency comparison, and
    web_filter_config and patch_releases share no join_field regardless
    (config_id vs patch_id, unrelated domains).
  - WF-009 "HTTPS/TLS inspection must be correctly configured": web_
    filter_config and certificates share no join_field (config_id vs
    cert_id) and no canonical field marks which certificate belongs to
    which TLS-inspection config.

Patch Management (PM-001, PM-002, PM-003, PM-008, PM-009, PM-010):
  - PM-001 "Critical security patches must be applied within SLA": needs
    both a relative-date window and a dynamic per-severity threshold
    (sla_rules.patch_within_days) — same combined gap as VM-002.
  - PM-002 "Unpatched systems must be identified": patch_inventory has no
    unique per-row identifier (see the module-level note above) — a safe
    self-join on its `installed` boolean isn't possible, and ThresholdRule
    can't take a boolean value directly either.
  - PM-003 "End-of-life/unsupported systems must be flagged": needs a
    relative-date comparison — the same gap as AS-005/VM-003, over the
    same system_inventory/support_matrix pair.
  - PM-008 "Patch compliance must be reconciled per device": same
    no-unique-row-id gap as PM-002, compounded by needing a 3-way
    per-asset-per-required-patch reconciliation on top.
  - PM-009 "Third-party/application patches must be tracked": software_
    inventory (asset_id, software_name, version) and patch_releases
    (patch_id, severity, released_at) share no join_field — nothing
    connects an installed software title to a specific patch release.
  - PM-010 "Patch exceptions/deferrals require documented justification":
    patch_exceptions (asset_id, reason) and exception_approvals
    (exception_id, approved_by, approved_at) share no join_field —
    patch_exceptions has no exception_id, so the generic exception_
    approvals table (built for web_filter_exceptions) can't be reused
    here despite the very similar-sounding name.

Remote Access (RA-002, RA-003, RA-004, RA-006, RA-007, RA-008, RA-009,
RA-010, RA-011, RA-012):
  - RA-002 "Remote access must be explicitly authorised": remote_access_
    grants has no per-grant unique id (only user_id, granted_at, approved —
    and a user could plausibly be granted, revoked, and re-granted over
    time), so a self-join on user_id risks the same multiplied-row problem
    as patch_inventory above; joining to access_requests instead would
    only be possible via the same coarse, non-grant-specific user_id match
    already rejected for AC-007/RA-001.
  - RA-003 "VPN access must be restricted to approved users": vpn_accounts
    has no employee_id field (only user_id) — matching to hr_employees
    needs a 3-way chain (vpn_accounts -> user -> employee).
  - RA-004 "Terminated employees must have remote access revoked": same
    3-way chain gap as RA-003 (employee -> user -> vpn_accounts; vpn_
    accounts has no employee_id to join against employee directly).
  - RA-006 "Remote access outside approved hours/geography should be
    reviewed": access_policies.allowed_countries is a LIST-typed field —
    testing "source_country not in allowed_countries" needs a list-
    membership operator, which doesn't exist (only eq/ne/gt/gte/lt/lte/
    is_null/is_not_null), on top of being a field-to-field comparison
    against another object's field rather than a literal.
  - RA-007 "Privileged remote access requires additional approval": needs
    a 3-way join (remote_access_logs -> user_roles -> privileged_access_
    approvals) plus the same missing "is this role privileged" flag gap
    as AC-005.
  - RA-008 "Third-party/vendor remote access must be time-bound and
    logged": vendor_access_grants (keyed by vendor) and remote_access_logs
    (keyed by user_id/session_id) share no join_field, and "time-bound"
    needs a relative-date (expires_at vs "now") comparison regardless.
  - RA-009 "Remote access tools must be from an approved catalogue":
    remote_access_tools.tool_name and approved_software.software_name are
    conceptually the same idea but literally different field names — the
    same join-alias gap as AS-001/NW-004, even though AS-004/WF-008 (same
    general "is this on the approved list" shape) ARE templatable because
    software_inventory and approved_software happen to share the literal
    "software_name" field name.
  - RA-010 "Idle remote sessions must time out": needs a field-to-field
    comparison (remote_access_logs.idle_minutes vs session_policies.
    max_idle_minutes) rather than a literal — and baking today's
    max_idle_minutes value (30) into the template as a hardcoded threshold
    would silently go stale the moment a client's policy value differs,
    the same discipline call as RA-010's siblings elsewhere in these
    migrations that reject inventing/baking in a magnitude value.
  - RA-011 "Failed remote login attempts must be monitored": neither
    remote_access_logs nor login_history has any success/failure outcome
    field at all — there's nothing to test "failed" against.
  - RA-012 "Split-tunnelling / unauthorised network bridging should be
    identified": vpn_config and network_policies are both effectively
    singleton config tables with no shared join_field, and the actual test
    (split_tunnel_enabled == True AND split_tunnel_allowed == False) is a
    field-to-field comparison across them regardless.

Revision ID: 0049
Revises: 0048
Create Date: 2026-09-15
"""
import json

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "NW-001",
        "Network connection is not approved",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "network_connections",
            "secondary_object": "network_connections",
            "join_field": "connection_id",
            "condition_primary": {"field": "approved", "operator": "eq", "value": False},
            "condition_secondary": {"field": "connection_id", "operator": "is_not_null"},
        },
    ),
    (
        "NW-002",
        "Firewall rule has no recorded approval",
        {"rule_type": "missing_match", "primary_object": "firewall_rules", "secondary_object": "firewall_approvals", "join_field": "rule_id"},
    ),
    (
        "WF-001",
        "Endpoint web filtering is not active",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "web_filter_status",
            "secondary_object": "web_filter_status",
            "join_field": "asset_id",
            "condition_primary": {"field": "filtering_active", "operator": "eq", "value": False},
            "condition_secondary": {"field": "asset_id", "operator": "is_not_null"},
        },
    ),
    (
        "WF-005",
        "Web filtering exception has no recorded approval",
        {"rule_type": "missing_match", "primary_object": "web_filter_exceptions", "secondary_object": "exception_approvals", "join_field": "exception_id"},
    ),
    (
        "WF-008",
        "Installed software is not on the approved list",
        {"rule_type": "missing_match", "primary_object": "software_inventory", "secondary_object": "approved_software", "join_field": "software_name"},
    ),
    (
        "WF-010",
        "Managed asset has no web-filtering status record at all",
        {"rule_type": "missing_match", "primary_object": "asset_register", "secondary_object": "web_filter_status", "join_field": "asset_id"},
    ),
    (
        "PM-004",
        "Patch deployment has not been approved",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "patch_deployments",
            "secondary_object": "patch_deployments",
            "join_field": "deployment_id",
            "condition_primary": {"field": "approved", "operator": "eq", "value": False},
            "condition_secondary": {"field": "deployment_id", "operator": "is_not_null"},
        },
    ),
    (
        "PM-005",
        "Patch deployment lacks test evidence",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "patch_deployments",
            "secondary_object": "patch_deployments",
            "join_field": "deployment_id",
            "condition_primary": {"field": "tested", "operator": "eq", "value": False},
            "condition_secondary": {"field": "deployment_id", "operator": "is_not_null"},
        },
    ),
    (
        "PM-006",
        "Emergency patch deployment lacks retrospective approval",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "patch_deployments",
            "secondary_object": "patch_deployments",
            "join_field": "deployment_id",
            "condition_primary": {"field": "emergency", "operator": "eq", "value": True},
            "condition_secondary": {"field": "approved", "operator": "eq", "value": False},
        },
    ),
    (
        "PM-007",
        "Patch deployment failure is unresolved",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "patch_incidents",
            "secondary_object": "patch_incidents",
            "join_field": "deployment_id",
            "condition_primary": {"field": "resolved", "operator": "eq", "value": False},
            "condition_secondary": {"field": "deployment_id", "operator": "is_not_null"},
        },
    ),
    (
        "RA-001",
        "Remote access session did not use MFA",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "remote_access_logs",
            "secondary_object": "remote_access_logs",
            "join_field": "session_id",
            "condition_primary": {"field": "mfa_used", "operator": "eq", "value": False},
            "condition_secondary": {"field": "session_id", "operator": "is_not_null"},
        },
    ),
    (
        "RA-005",
        "Remote access session has no matching session-audit record",
        {"rule_type": "missing_match", "primary_object": "remote_access_logs", "secondary_object": "session_audit", "join_field": "session_id"},
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
