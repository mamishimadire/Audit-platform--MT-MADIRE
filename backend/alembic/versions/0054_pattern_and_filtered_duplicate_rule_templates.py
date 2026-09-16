"""Extend rule-template coverage using FieldCondition's "matches" (regex)
operator and DuplicateRule.condition (pre-filtered duplicate detection) —
the third and closing migration of the third and final rule-template pass
(0052-0054, following 0044, 0046-0049, and 0050-0051).

Same validation discipline as every prior rule-template migration: every
rule_definition here was schema-validated against app.schemas.test_rule's
Pydantic models AND dry-run against BOTH engines (app.services.
rule_evaluation and gateway.gateway.rule_engine) with synthetic good/bad
rows, confirming each produces exactly the expected exception set in both.

=== Templated in this migration ===

User Access Management:
  - AC-009 "Generic accounts must be identified": cross_match_condition,
    self-join on user.user_id (a genuine per-row key), condition_primary
    username matches "(?i)(admin|generic|shared|guest|service|test)"
    (case-insensitive, substring match on any of the common generic-
    account name patterns), condition_secondary a pass-through
    (user_id is_not_null) — the same "boolean self-filter" self-join shape
    0046 established, just with a regex condition instead of a literal
    equality/boolean one. Confirmed against the seed fixture: matches
    U013 ("admin") and U014 ("shared.finance"), and does not match any
    genuine personal username (e.g. "t.nkosi").

API Controls:
  - API-003 "Failed API authentication should be monitored": duplicate,
    api_logs, group_by source_ip, condition status == "auth_failed". The
    condition is what makes this safe to template at all: an unfiltered
    duplicate check on (source_ip) alone would also flag ordinary repeated
    SUCCESSFUL requests from the same IP as "unusual failed requests" — a
    wrong result, which is exactly why 0048/0049 refused this control
    before DuplicateRule.condition existed. Confirmed against the seed
    fixture: the three auth_failed rows from 203.0.113.5 are flagged; the
    one success row from the same IP is not.

Web Filtering / Content Security:
  - WF-004 "Repeated attempts to access malicious sites should be
    investigated": duplicate, web_filter_logs, group_by (user_id, url),
    condition action == "blocked". Same reasoning as API-003 — without the
    condition, repeated normal ALLOWED visits to the same URL would also
    be flagged, which 0049 already refused. Confirmed against the seed
    fixture: U006's three blocked attempts at known-malware-site.example
    are flagged.

=== Final accounting: every one of the 59 controls this pass targeted ===

98 controls were templated before this pass began (0044, 0046-0049,
0050-0051). This pass (0052-0054) adds 12 more:
  AC-009, API-003, GL-008, OP-004, OTC-006, PM-010, PR-011, PR-014, RA-004,
  RA-011, SOD-003, WF-004
bringing the total to 110/157. The remaining 47 of the original 59 are
listed below, grouped by the specific, precise reason each is still
blocked — a real, named engine or data-model gap, not a restatement of
"too hard." Several were detailed at length in 0052/0053's own docstrings
(the 3-way-anti-join and 4-object-chain findings from 0052; the field-
addition rejections from 0053) and are only summarized here for a complete
single accounting; see those migrations for the full reasoning.

--- 3-way anti-join (ThreeWayMatchRule performs inner joins only — see
    0052's central finding) ---
  - AC-005, API-002, RA-003, RA-007: detailed in 0052.
  - MD-005, DP-002, PY-010: the audit_logs polymorphic entity_type/
    entity_id join gap, unchanged since 0046/0051 — filtering audit_logs
    down to the rows for ONE entity_type before joining is itself a
    pre-join FILTER (which ThreeWayMatchRule's condition_secondary/
    condition_tertiary can now genuinely do, unlike missing_match's
    primary-only primary_condition), but the actual test these three
    controls need ("was this change logged AT ALL") is still an anti-join
    once that filter is applied, which is the same capability
    ThreeWayMatchRule lacks — filtering which rows are ELIGIBLE to join
    does not create a "found no match" capability where none exists.

--- 4-object chain (needs one more hop than ThreeWayMatchRule's fixed
    primary/secondary/tertiary shape supports) ---
  - GL-002, GL-007, PR-002, PR-019, PM-008: detailed in 0052.

--- Dynamic relative-date (a per-row offset looked up from another table's
    field, e.g. backup_retention_rules.retention_days or sla_rules.
    remediate_within_days, combined with "now" — distinct from
    RelativeDate, which is a single fixed offset baked in at template-
    authoring time, and distinct from field_comparison, which compares two
    raw field values but has no "plus N days" arithmetic) ---
  - BK-002 "Backups must run according to schedule": backup_schedules.
    frequency is additionally a free-text category ("daily"), not even a
    number, compounding the gap.
  - BK-004 "Backup retention requirements must be met": backup_retention_
    rules.retention_days is a genuine per-system number, but comparing
    run_at against "now minus that number of days" is exactly the
    unaddressed capability.
  - OP-007 "Logs must be retained": same shape, log_retention_rules.
    retention_days.
  - PM-001 "Critical security patches must be applied within SLA" and
    VM-002 "Critical vulnerabilities must be remediated within SLA": both
    need this AND a dynamic per-severity threshold (sla_rules.
    patch_within_days / remediate_within_days) — the same underlying gap,
    just reached via a severity lookup instead of a system_id lookup.
  - DP-004 "Data retention requirements must be followed": has this gap
    compounded with a missing "dataset" field on data_records (considered
    and deliberately not added — see 0053).
  This is a genuine, real, recurring gap across 6 controls that no round
  of engine capabilities added — noted here explicitly per this project's
  instruction to report (not silently fix) a remaining engine gap found
  during this pass.

--- No value correspondence at all (not a naming/join-alias problem —
    confirmed no field on either side holds a value that corresponds to
    the other, so secondary_join_field and ThreeWayMatchRule's join
    aliasing genuinely cannot help) ---
  - AS-001 "IT assets must be recorded": asset_register (AST-prefixed) vs.
    system_inventory (SYS-prefixed) — different ID schemes; considered for
    a canonical field bridge and rejected in 0053 as fabricating a link
    rather than filling an attribute gap.
  - NW-004 "Network devices must be inventoried": network_devices vs.
    asset_register — same class of gap; also considered and rejected in
    0053.
  - OP-005 "System incidents must be logged": incidents has no "system"
    field, system_logs has no "incident_id" field.
  - EN-004 "Weak encryption protocols must be identified": system_
    configurations (system_id) vs. security_baselines (a generic "control"
    policy-label field, not a per-system key) — and even joined, tls_
    version vs. required_value would be a version-string comparison, not a
    literal eq.
  - PM-009 "Third-party/application patches must be tracked": software_
    inventory vs. patch_releases — nothing connects an installed software
    title to a specific patch release.
  - VM-003 "Unsupported systems must be identified": assets/software_
    inventory have no path to support_matrix at any hop.
  - WF-003 "Blocked access attempts must be logged": web_filter_logs vs.
    security_logs — no linking id at all.
  - WF-006 "Filtering must apply consistently on and off network": web_
    filter_status (asset-keyed) vs. remote_access_logs (user/session-
    keyed) — bridging through asset_register/employee/user would need 5
    objects, far past even the 4-object gap above.
  - WF-009 "HTTPS/TLS inspection must be correctly configured": web_
    filter_config vs. certificates — no field marks which certificate
    belongs to which TLS-inspection config.

--- Aggregate/statistical (no primitive computes SUM/COUNT/AVG or a
    distinct-value count across rows; unaffected by any of the three
    capability rounds) ---
  - AC-008 "Shared accounts should be restricted": needs a DISTINCT count
    of person/IP per account, not a row-count duplicate check — re-
    confirmed this pass per the task brief's specific prompt to re-examine
    it; DuplicateRule.condition filters WHICH rows are considered, it does
    not change what "duplicate" counts (repeated rows on the group_by
    key), so it cannot express "used by more than one distinct value of a
    DIFFERENT column."
  - GL-009 "Debits and credits must balance": SUM(debit) vs SUM(credit)
    per journal_id.
  - GL-010 "GL should reconcile to subledger": multi-table SUM
    reconciliation.
  - PY-008 "Unusual salary increases should be investigated": needs a
    period-over-period (this row vs. the SAME employee's PRIOR period)
    comparison — re-examined for a safe self-join this pass (the way
    AC-009/RA-004 above use one) and rejected: payroll_history has no
    unique per-row key (an employee has one row per period), so a self-
    join on employee_id alone would pair every period against every OTHER
    period for that employee, not specifically the immediately preceding
    one, producing spurious/multiplied comparisons — the same correctness
    risk 0049 already identified for patch_inventory-shaped self-joins.
  - VM-001 "Vulnerabilities must be identified": describes a data-
    ingestion process, not a row-level condition — no condition exists to
    bind any primitive to, aggregate or otherwise.

--- Business-mapping / would invent an undefined client policy ---
  - CM-006 "Changes outside approved window require review": deployed_at
    (a timestamp) vs. change_requests.scheduled_window (a category like
    "weekend") — a category-to-time-window translation, not a comparison
    any operator here performs.
  - GL-006 "Unusual journals should be investigated": audit_procedure
    names no single concrete, testable condition.
  - NW-003 "Excessively permissive firewall rules should be identified":
    the seeded "violation" (port 3389 open to 0.0.0.0/0) is a specific
    business rule with no canonical representation.
  - OTC-008 "Bad debts should be monitored": detailed in 0053 (no
    materiality/SLA concept for receivables aging).
  - WF-002 "Malicious/blacklisted categories must be blocked by policy":
    web_filter_policies.category <-> security_baselines.control is a
    business-name mapping, not a literal shared value or a regex-
    expressible relationship (FieldCondition's "matches" compares a field
    to a fixed pattern, not two different objects' fields to each other).

--- No unique per-row key (a safe self-join/dedup needs a genuine
    one-row-per-value key; these tables' natural keys repeat) ---
  - PM-002 "Unpatched systems must be identified": patch_inventory's key
    is the (asset_id, patch_id) pair.
  - PM-008: also listed under the 4-object-chain and no-unique-key gaps
    together (see 0052 and above).
  - RA-002 "Remote access must be explicitly authorised": remote_access_
    grants has no per-grant id (only user_id/granted_at/approved).

--- Dynamic list-field lookup, or no join key for one at all (distinct
    from "in"/"not_in", which only test a field against a FIXED literal
    list baked into the template — not a list read dynamically from
    another table's row, which would have the same "would go stale"
    problem 0049 already rejected for RA-010's hardcoded max_idle_minutes) ---
  - RA-006 "Remote access outside approved hours/geography should be
    reviewed": access_policies.allowed_countries would have to be read
    dynamically per policy, not hardcoded as a literal "in" list.
  - RA-010 "Idle remote sessions must time out": session_policies has no
    session_id/user_id at all to join against remote_access_logs in the
    first place — the dynamic-lookup problem is compounded by there being
    no join to perform.
  - RA-012 "Split-tunnelling / unauthorised network bridging should be
    identified": vpn_config and network_policies are both singleton
    config tables with no shared join field, same compounding as RA-010.

--- Missing-canonical-field gap considered and deliberately NOT filled
    (see 0053's "field additions rejected" section for the full reasoning
    on each) ---
  - SOD-005 "Employee creation and payroll processing should be
    segregated", SOD-006 "Customer creation and credit approval should be
    segregated": would need a creator field on a master-data object,
    which no object of that kind carries anywhere in the model.

--- Structurally deeper than list-membership alone (re-examined per the
    task brief's specific prompt) ---
  - SOD-007 "Identify conflicting roles": needs a PAIRWISE dynamic
    comparison against sod_rules.conflicting_permissions (a 2-element list
    per rule) — i.e. does some role hold BOTH list[0] AND list[1] of the
    SAME sod_rules row. "in"/"not_in" test one field against a fixed
    literal list; they cannot express "this row's value equals element 0
    of some other table's list field, AND a second row of the same role
    has a value equal to element 1 of that SAME row's list field." That is
    list-indexing combined with a self-join keyed off dynamic data, which
    is a different and deeper capability than list-membership alone
    provides. SOD-001 (0050) solves the one case in the seed data this
    control's own conflict matrix defines by hardcoding the two literal
    permission values instead of reading them from sod_rules — templating
    SOD-007 the same way for its OTHER row (create_purchase_order /
    approve_purchase_order) would just be re-deriving a second, differently
    -coded copy of the same technique, not a generic solution, and neither
    approve_purchase_order nor any PO-approval permission actually exists
    anywhere in role_permissions to test against — so even the hardcoded
    shortcut has no real data to produce a correct result from.

--- Still blocked for the reason already given in the prior pass, re-
    confirmed but unchanged (missing_match's secondary side still has no
    equivalent of primary_condition) ---
  - AC-001 "User access must be approved": needs the SECONDARY object of a
    missing_match filtered (only an access_request with status ==
    "approved" should count as a match; ARQ003/U006 exists but is
    "pending," so an unfiltered anti-join would wrongly treat it as
    satisfying the check) — this is a different, still-missing capability
    from primary_condition, and no round-3 addition touches missing_match
    at all.

--- Still blocked for the reason already given in the prior pass, re-
    confirmed but unchanged (no capability from any of the three rounds
    applies) ---
  - OP-003 "Critical jobs must run according to schedule": re-examined per
    this pass's mandate to check every control individually rather than
    trust the prior category guess. scheduled_jobs.schedule remains a
    free-text cadence string ("daily 02:00"/"monthly"/"weekly") with no
    canonical numeric-window mapping; none of matches/in/not_in/
    DuplicateRule.condition/ThreeWayMatchRule changes that. Templating an
    existence-only version (does JOB003 have ANY job_executions row ever)
    would test a materially narrower thing than "runs according to
    schedule," the same shortcut 0051 already rejected.

Every one of the 59 controls this pass targeted is accounted for exactly
once: 12 templated (this migration + 0052 + 0053) or listed above with a
specific reason. None were silently dropped.

Revision ID: 0054
Revises: 0053
Create Date: 2026-09-16
"""
import json

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "AC-009",
        "Username matches a generic/shared-account pattern",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "user",
            "secondary_object": "user",
            "join_field": "user_id",
            "condition_primary": {"field": "username", "operator": "matches", "value": "(?i)(admin|generic|shared|guest|service|test)"},
            "condition_secondary": {"field": "user_id", "operator": "is_not_null"},
        },
    ),
    (
        "API-003",
        "Repeated failed API authentication attempts from the same source",
        {
            "rule_type": "duplicate",
            "object": "api_logs",
            "group_by": ["source_ip"],
            "condition": {"field": "status", "operator": "eq", "value": "auth_failed"},
        },
    ),
    (
        "WF-004",
        "Repeated blocked attempts to access the same site",
        {
            "rule_type": "duplicate",
            "object": "web_filter_logs",
            "group_by": ["user_id", "url"],
            "condition": {"field": "action", "operator": "eq", "value": "blocked"},
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
