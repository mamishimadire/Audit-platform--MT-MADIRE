"""Adds two more rule-engine primitives (BaselineComparisonRule,
ReconciliationRule) and templates 23 more controls — the rest of the
gap after migration 0076, minus 4 that genuinely need a capability this
system does not have yet (see the bottom of this docstring).

=== BaselineComparisonRule ===
security_baselines/session_policies/network_policies are flat,
name-keyed (or single-row) config tables with NO per-row join key to
whatever they're being checked against — missing_match/cross_match_
condition/three_way_match all require an actual shared key on both
sides, which simply doesn't exist here (system_configurations has no
"control" column to join security_baselines.control against; it's one
row per system with a single tls_version field). BaselineComparisonRule
looks up ONE row from a lookup object (optionally filtered to a named
setting via baseline_key_field/baseline_key_value, or just "the row" for
a single-row policy table) and compares every (optionally pre-filtered)
row of the object being checked against that one looked-up value.
Verified against both engines with a firewall_rules/security_baselines
scenario: a rule with source="any" is flagged when the baseline says
"any" is the disallowed pattern, a restricted-source rule is not.

=== ReconciliationRule ===
GL-010 needs "does this GL account's balance equal the SUM of this
account's subledger transactions" — sums subledger_value_field grouped
by subledger_key_field, flags every ledger row whose own value doesn't
match the corresponding group's total. Distinct from BalanceRule (sums
two fields WITHIN one object, e.g. debits vs. credits on the same rows);
this compares one object's aggregate against a stored value on a
DIFFERENT object. Verified against both engines: an account whose
balance matches its summed transactions does not flag, one that's off
does.

=== The 23 templates ===
AS-001, CM-006, EN-004, NW-003, WF-002, OP-003, OP-005, OTC-008,
PM-002, PM-008, PM-009, PY-008, RA-002, RA-003, RA-010, RA-012,
SOD-005, SOD-006, VM-001, VM-003, WF-006, WF-009, GL-010 — each uses
either an existing primitive in a way already established elsewhere in
this library (e.g. PY-008 and VM-001 both self-join on their own row,
the same pattern GL-008 already uses for suspense-account balances) or
one of the two new ones above. Three required_tables entries were
missing a table their own rule genuinely needs and are corrected here
(PY-008: salary_changes: the payroll_history/hr_employees pairing in
its own required_tables was never actually usable for a same-row
before/after salary comparison — salary_changes already models exactly
that, old_salary/new_salary on one row; SOD-005/SOD-006: role_permissions,
mirroring SOD-001's own already-established creator/approver-conflict
pattern, which SOD-005/006 need the same way; VM-003: system_inventory,
the only object in the canonical model that actually carries an "os"
field to compare against support_matrix).

A few templates document an assumption a client can edit afterward
(every generated rule becomes an ordinary, editable TestRule row, not a
fixed contract) rather than a fabricated business specific: CM-006
assumes change_requests.scheduled_window's approved value is literally
"standard"; SOD-005/006 assume role_permissions.permission values named
"create_employee"/"process_payroll"/"create_customer"/"approve_credit",
following SOD-001's exact naming convention for "create_supplier"/
"approve_payment" since no other convention exists to check against.

=== Still not templated — a real capability gap, not a deferral ===
- RA-006 "outside approved hours/geography": would need a SPECIFIC
  allowed-country list or hour range with no universal default —
  unlike "standard" or "active", there's no convention to reasonably
  guess, and hour-of-day extraction from a timestamp isn't an operator
  this engine has either. Guessing a country list would silently test
  the wrong policy, not merely an approximate one.
- RA-007 "privileged remote access without elevated approval": needs a
  3-object ANTI-join (session -> is this user privileged (user_roles)?
  -> does NO privileged_access_approvals row exist for this session?).
  missing_match only anti-joins 2 objects; dropping the privileged-role
  filter to fit 2 objects would silently test "no remote session lacks
  approval" instead of the control's actual, narrower claim.
- SOD-007 "conflicting roles" / "compare against predefined SoD conflict
  matrix": sod_rules is a TABLE of conflict pairs to check against, not
  one fixed pair — every rule_definition here is static (authored once,
  same check every run); testing against a data-driven set of pairs
  needs the engine itself to loop over sod_rules at execution time, a
  different kind of capability than anything built so far.
- WF-003 "blocked events missing from the log": web_filter_logs and
  security_logs share no key at all (no common user/asset/session
  identifier on either side) — there's nothing to join on, and guessing
  one would silently correlate unrelated events.

Final rule-template coverage: 153/157 (was 130/157 after migration
0076).

Revision ID: 0077
Revises: 0076
Create Date: 2026-09-18
"""
import json

from alembic import op
from sqlalchemy import text

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


_REQUIRED_TABLES_ADDITIONS = {
    "PY-008": "salary_changes",
    "SOD-005": "role_permissions",
    "SOD-006": "role_permissions",
    "VM-003": "system_inventory",
}

_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "AS-001",
        "Discovered system has no asset register entry",
        {
            "rule_type": "missing_match",
            "primary_object": "system_inventory",
            "secondary_object": "asset_register",
            "join_field": "system_id",
            "secondary_join_field": "asset_id",
        },
    ),
    (
        "CM-006",
        "Deployment linked to a change outside the standard window",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "deployments",
            "secondary_object": "change_requests",
            "join_field": "change_id",
            "condition_primary": {"field": "deployed_at", "operator": "is_not_null"},
            "condition_secondary": {"field": "scheduled_window", "operator": "ne", "value": "standard"},
        },
    ),
    (
        "EN-004",
        "System TLS version does not match the required baseline",
        {
            "rule_type": "baseline_comparison",
            "object": "system_configurations",
            "field": "tls_version",
            "operator": "ne",
            "baseline_object": "security_baselines",
            "baseline_key_field": "control",
            "baseline_key_value": "tls_version",
            "baseline_value_field": "required_value",
        },
    ),
    (
        "NW-003",
        "Firewall rule allows the baseline's disallowed source pattern",
        {
            "rule_type": "baseline_comparison",
            "object": "firewall_rules",
            "field": "source",
            "operator": "eq",
            "baseline_object": "security_baselines",
            "baseline_key_field": "control",
            "baseline_key_value": "unrestricted_source_pattern",
            "baseline_value_field": "required_value",
            "condition": {"field": "action", "operator": "eq", "value": "allow"},
        },
    ),
    (
        "WF-002",
        "Blacklisted filter category is not set to blocked",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "web_filter_policies",
            "secondary_object": "security_baselines",
            "join_field": "category",
            "secondary_join_field": "control",
            "condition_primary": {"field": "blocked", "operator": "eq", "value": False},
            "condition_secondary": {"field": "required_value", "operator": "eq", "value": "true"},
        },
    ),
    (
        "OP-003",
        "Critical job has no recorded execution",
        {
            "rule_type": "missing_match",
            "primary_object": "scheduled_jobs",
            "secondary_object": "job_executions",
            "join_field": "job_id",
            "primary_condition": {"field": "critical", "operator": "eq", "value": True},
        },
    ),
    (
        "OP-005",
        "Incident has no corresponding system log entry",
        {
            "rule_type": "missing_match",
            "primary_object": "incidents",
            "secondary_object": "system_logs",
            "join_field": "system_id",
            "secondary_join_field": "system",
        },
    ),
    (
        "OTC-008",
        "Receivable balance is overdue beyond the monitoring threshold",
        {
            "rule_type": "threshold",
            "object": "receivables",
            "field": "days_overdue",
            "operator": "gt",
            "value": {"kind": "parameter", "key": "bad_debt_overdue_days_threshold", "default": 90, "multiplier": 1},
        },
    ),
    (
        "PM-002",
        "Catalogued patch is not installed",
        {"rule_type": "threshold", "object": "patch_inventory", "field": "installed", "operator": "eq", "value": False},
    ),
    (
        "PM-008",
        "Asset has no installed-patch record at all",
        {
            "rule_type": "missing_match",
            "primary_object": "asset_register",
            "secondary_object": "patch_inventory",
            "join_field": "asset_id",
            "secondary_condition": {"field": "installed", "operator": "eq", "value": True},
        },
    ),
    (
        "PM-009",
        "Installed software has no tracked patch release history",
        {
            "rule_type": "missing_match",
            "primary_object": "software_inventory",
            "secondary_object": "patch_releases",
            "join_field": "software_name",
        },
    ),
    (
        "PY-008",
        "Salary change is an increase from the prior amount",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "salary_changes",
            "secondary_object": "salary_changes",
            "join_field": "change_id",
            "condition_primary": {"field": "new_salary", "operator": "is_not_null"},
            "condition_secondary": {"field": "old_salary", "operator": "is_not_null"},
            "field_comparison": {"primary_field": "new_salary", "operator": "gt", "secondary_field": "old_salary"},
        },
    ),
    (
        "RA-002",
        "Remote access grant is not marked approved",
        {"rule_type": "threshold", "object": "remote_access_grants", "field": "approved", "operator": "eq", "value": False},
    ),
    (
        "RA-003",
        "VPN account has no approved access request on file",
        {
            "rule_type": "missing_match",
            "primary_object": "vpn_accounts",
            "secondary_object": "access_requests",
            "join_field": "user_id",
            "secondary_condition": {"field": "status", "operator": "eq", "value": "approved"},
        },
    ),
    (
        "RA-010",
        "Remote session idle time exceeds the policy limit",
        {
            "rule_type": "baseline_comparison",
            "object": "remote_access_logs",
            "field": "idle_minutes",
            "operator": "gt",
            "baseline_object": "session_policies",
            "baseline_value_field": "max_idle_minutes",
        },
    ),
    (
        "RA-012",
        "VPN split-tunnelling setting does not match network policy",
        {
            "rule_type": "baseline_comparison",
            "object": "vpn_config",
            "field": "split_tunnel_enabled",
            "operator": "ne",
            "baseline_object": "network_policies",
            "baseline_value_field": "split_tunnel_allowed",
        },
    ),
    (
        "SOD-005",
        "One role can both create employees and process payroll",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "role_permissions",
            "secondary_object": "role_permissions",
            "join_field": "role",
            "condition_primary": {"field": "permission", "operator": "eq", "value": "create_employee"},
            "condition_secondary": {"field": "permission", "operator": "eq", "value": "process_payroll"},
        },
    ),
    (
        "SOD-006",
        "One role can both create customers and approve credit",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "role_permissions",
            "secondary_object": "role_permissions",
            "join_field": "role",
            "condition_primary": {"field": "permission", "operator": "eq", "value": "create_customer"},
            "condition_secondary": {"field": "permission", "operator": "eq", "value": "approve_credit"},
        },
    ),
    (
        "VM-001",
        "Critical vulnerability is still open",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "vulnerabilities",
            "secondary_object": "vulnerabilities",
            "join_field": "vuln_id",
            "condition_primary": {"field": "severity", "operator": "eq", "value": "critical"},
            "condition_secondary": {"field": "status", "operator": "eq", "value": "open"},
        },
    ),
    (
        "VM-003",
        "System OS is past its support matrix end date",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "system_inventory",
            "secondary_object": "support_matrix",
            "join_field": "os",
            "condition_primary": {"field": "os", "operator": "is_not_null"},
            "condition_secondary": {"field": "supported_until", "operator": "lt", "value": {"kind": "relative_date", "relative_days": 0}},
        },
    ),
    (
        "WF-006",
        "Filtering is inactive for an off-network device",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "web_filter_status",
            "secondary_object": "web_filter_status",
            "join_field": "asset_id",
            "condition_primary": {"field": "on_network", "operator": "eq", "value": False},
            "condition_secondary": {"field": "filtering_active", "operator": "eq", "value": False},
        },
    ),
    (
        "WF-009",
        "Filter category database has not been updated recently",
        {
            "rule_type": "threshold",
            "object": "web_filter_config",
            "field": "updated_at",
            "operator": "lte",
            "value": {"kind": "relative_date", "relative_days": -30},
        },
    ),
    (
        "GL-010",
        "GL account balance does not reconcile to AP subledger total",
        {
            "rule_type": "reconciliation",
            "ledger_object": "general_ledger",
            "ledger_key_field": "account",
            "ledger_value_field": "balance",
            "subledger_object": "ap_transactions",
            "subledger_key_field": "account",
            "subledger_value_field": "amount",
        },
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    for control_code, table_to_add in _REQUIRED_TABLES_ADDITIONS.items():
        bind.execute(
            text(
                """
                UPDATE control_library
                SET required_tables = required_tables || to_jsonb(CAST(:table_to_add AS text))
                WHERE control_code = :control_code
                  AND NOT (required_tables @> to_jsonb(CAST(:table_to_add AS text)))
                """
            ),
            {"control_code": control_code, "table_to_add": table_to_add},
        )

    for control_code, rule_name, rule_definition in _TEMPLATES:
        rule_definition_json = json.dumps(rule_definition).replace("'", "''")
        rule_name_escaped = rule_name.replace("'", "''")
        op.execute(
            f"""
            INSERT INTO control_rule_templates (control_library_id, rule_name, rule_definition)
            SELECT control_library_id, '{rule_name_escaped}', '{rule_definition_json}'
            FROM control_library WHERE control_code = '{control_code}'
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

    bind = op.get_bind()
    for control_code, table_to_remove in _REQUIRED_TABLES_ADDITIONS.items():
        bind.execute(
            text(
                """
                UPDATE control_library
                SET required_tables = (
                    SELECT jsonb_agg(t) FROM jsonb_array_elements(required_tables) AS t
                    WHERE t <> to_jsonb(CAST(:table_to_remove AS text))
                )
                WHERE control_code = :control_code
                """
            ),
            {"control_code": control_code, "table_to_remove": table_to_remove},
        )
