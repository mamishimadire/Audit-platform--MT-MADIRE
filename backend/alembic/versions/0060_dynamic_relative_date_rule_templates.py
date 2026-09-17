"""Extend rule-template coverage, round 4, part 2: adds
DynamicRelativeDateComparison (on CrossMatchConditionRule) and its
three-way counterpart ThreeWayDynamicRelativeDateComparison (on
ThreeWayMatchRule) — see app/schemas/test_rule.py's module docstring and
each class's own docstring for the full design rationale — templates the
5 controls this newly unblocks, adds one canonical field, and closes out
this pass with the complete final accounting of every control still
blocked, continuing 0059 (MissingMatchRule.secondary_condition; AC-001,
AC-005, MD-005, DP-002, PY-010).

=== The gap this closes ===
0051 and 0054 catalogued a recurring "dynamic relative-date" gap across 6
controls: each needs to compare a date field to "now," but the number of
days involved is not one fixed number decided when the template was
written — it is a genuine per-row (or per-severity, or per-dataset) value
that lives in a SECOND table and can change independently of the rule
(e.g. a client editing their own backup_retention_rules or sla_rules).
RelativeDate (0050) only ever bakes ONE fixed day count into the template
at authoring time — it cannot vary per row. FieldComparison/
ThreeWayFieldComparison (0050/0052) compare two raw field values against
each other, with no "now() +/- N days" arithmetic at all. Neither
primitive, nor ParameterReference (0055 — a single org-wide tunable
constant, still the same number for every row), can express "the offset
itself is a per-row lookup." This is precisely the gap this project's task
brief asked to seriously evaluate building a real capability for.

DynamicRelativeDateComparison (2-object, added to CrossMatchConditionRule)
and ThreeWayDynamicRelativeDateComparison (3-object, added to
ThreeWayMatchRule) close it: they compare a date field on one side of a
join to now() offset by a day count read from a field on the (possibly
different) other side of the SAME joined row. Both are optional and
independent of field_comparison/ThreeWayFieldComparison — a rule may use
either, both, or neither — so no existing template's behaviour changes.
Implemented identically in both engines:
  - app/services/rule_evaluation.py: a shared _dynamic_relative_date_matches
    helper, called once per candidate joined row-pair (cross_match_condition)
    or row-triple (three_way_match) after any field_comparison check, using
    the same is_null-safe non-matching-on-missing/non-numeric-data
    convention _matches already uses for RelativeDate.
  - gateway/gateway/rule_engine.py: a shared _dynamic_relative_date_mask
    helper operating on whole columns (pd.to_datetime/pd.to_numeric with
    errors="coerce", so a bad row never raises, it just never matches),
    applied as a post-merge filter — via the same guaranteed-unique
    "__dyn_date__"/"__dyn_offset__" rename trick field_comparison already
    uses for the 2-object case, and directly via the already-unique
    role-suffixed column names (populated before either merge) for the
    3-object case.

=== Why only 5 of the original 6 controls, and one extra field ===
  - BK-004, OP-007, PM-001, VM-002: each needs exactly this capability and
    nothing else — templated below.
  - DP-004: needs this capability AND a "dataset" field on data_records
    (retention_rules is keyed per-dataset; data_records had no field
    identifying which dataset a record belongs to at all — 0053
    deliberately declined to add this in isolation because it would only
    have fixed one of DP-004's two compounding gaps at the time, the other
    being this exact missing capability). With the capability now built,
    the field is worth adding: every sibling classification/retention rule
    in this model already keys against a "dataset" attribute that lives on
    the record it classifies (data_classification.dataset, user_access.
    dataset) — data_records was the one record-level object missing its
    own copy of the SAME attribute, the same "obviously missing attribute"
    pattern that justified account_type (GL-008), max_duration_seconds
    (OP-004), and patch_exceptions.exception_id in prior rounds, not a new
    kind of field being invented. Added to app/core/canonical_model.py
    with its own inline justification comment. DP-004 is now templated.
  - BK-002 "Backups must run according to schedule": re-examined and NOT
    reclassified. backup_schedules.frequency is a free-text CATEGORY
    ("daily"/"weekly"/"monthly"), not a number of days — the day-count
    offset side of this new capability is defined as a numeric field read
    directly off a row; it has no string-to-days translation step, and
    deliberately so (a fixed "daily"->1/"weekly"->7/"monthly"->30 mapping
    would be a hardcoded categorical guess at a vocabulary real client
    scheduler data is not guaranteed to use, "monthly" itself is ambiguous
    at the day-count level, and FieldCondition/DynamicRelativeDateComparison
    have no "lookup mapping" value type to express such a translation even
    if the vocabulary were safe to assume). BK-002 remains blocked; see the
    final accounting below.

=== Validation discipline ===
Every rule_definition below was schema-validated against
app.schemas.test_rule's Pydantic models (TestRuleDefinition, via
pydantic.TypeAdapter) AND dry-run against BOTH engines with synthetic
good/bad rows, confirming each produces exactly the expected exception set
in both — including, for PM-001/VM-002, a deliberately duplicated
sla_rules "decoy" row for the same severity carrying only the OTHER SLA
field (remediate_within_days when patch_within_days is needed, and vice
versa) to specifically exercise the condition_tertiary/condition_secondary
is_not_null guard that picks the correct row out of a table where a real
client's row shape may not carry every SLA field on every row (see the
throwaway scripts used during this pass; not checked in, per the existing
project convention of ephemeral validation scripts).

=== Templated in this migration ===
  - BK-004 "Backup retention requirements must be met": cross_match_
    condition, backup_jobs <-> backup_retention_rules on system_id (pass-
    through conditions on both sides), dynamic_relative_date_comparison:
    run_at < now() - retention_days.
  - OP-007 "Logs must be retained": cross_match_condition, system_logs <->
    log_retention_rules on system (pass-through conditions on both sides),
    dynamic_relative_date_comparison: logged_at < now() - retention_days.
  - DP-004 "Data retention requirements must be followed": cross_match_
    condition, data_records <-> retention_rules on dataset (pass-through
    conditions on both sides), dynamic_relative_date_comparison:
    created_at < now() - retention_days. Uses the new data_records.dataset
    canonical field.
  - PM-001 "Critical security patches must be applied within SLA":
    three_way_match, patch_inventory (condition_primary installed ==
    False) <-> patch_releases on patch_id <-> sla_rules on severity
    (condition_tertiary patch_within_days is_not_null), three-way
    dynamic_relative_date_comparison: secondary.released_at < now() -
    tertiary.patch_within_days.
  - VM-002 "Critical vulnerabilities must be remediated within SLA":
    cross_match_condition, vulnerabilities (condition_primary status ==
    "open") <-> sla_rules on severity (condition_secondary
    remediate_within_days is_not_null), dynamic_relative_date_comparison:
    discovered_at < now() - remediate_within_days. Needs only 2 objects,
    not 3 — unlike PM-001, vulnerabilities already carries its own
    severity and discovered_at directly, so no patch_releases-equivalent
    intermediate object is needed to reach them.

Brings coverage from 115/157 (after 0059) to 120/157.

=== Final accounting: every one of the 47 controls untemplated as of
    migration 0054, re-derived from the live control_library/
    control_rule_templates tables at the start of this pass ===

Templated in 0059 (5): AC-001, AC-005, MD-005, DP-002, PY-010.
Templated in this migration (5): BK-004, OP-007, DP-004, PM-001, VM-002.
10 of the original 47 are now templated; 37 remain blocked, for the
specific, re-verified reasons below (grouped by reason, matching 0054's
own accounting style).

--- 3-way anti-join (an INNER join chained into an ANTI-join at the far
    end — ThreeWayMatchRule performs inner joins only, and
    MissingMatchRule's anti-join is 2-object-only; neither this pass's
    secondary_condition nor DynamicRelativeDateComparison changes this,
    since both are filters/comparisons applied WITHIN an existing join
    shape, not a new join-cardinality capability) ---
  - API-002 "API access must be authorised": re-checked directly against
    the seed fixture's own contractor case (U012) — it already has a
    user_roles row (ap_clerk) AND that role already has a role_permissions
    entry (create_supplier_invoice), so a plain 2-object anti-join against
    user_roles (the fix that worked for AC-005) would incorrectly clear
    it. The real gap — no permission value anywhere in the model
    represents "API access" specifically, so there is nothing to filter
    role_permissions to even with secondary_condition — is a business-
    mapping gap independent of, and in addition to, the join-depth
    problem. Still blocked.
  - RA-003 "VPN access must be restricted to approved users": no seed-
    fixture violation exists to confirm which of several plausible
    readings (no valid employee record vs. no approved access_request vs.
    both) is the control's real intended shape — guessing risks exactly
    the "plausible-looking but wrong template" this project's one hard
    constraint forbids. Still unattempted, still blocked.
  - RA-007 "Privileged remote access requires additional approval": needs
    an INNER join (remote_access_logs -> user_roles, to identify sessions
    belonging to a privileged user) chained into an ANTI-join
    (remote_access_logs -> privileged_access_approvals, to find sessions
    with no elevated approval on file) — the anti-join sits at the far end
    of what is structurally a 3-object chain. secondary_condition only
    helps MissingMatchRule's own single anti-join step; it does not grant
    missing_match a second, preceding INNER join to a third object. Still
    blocked.

--- 4-object chain (needs one more hop than ThreeWayMatchRule's fixed
    primary/secondary/tertiary shape supports; unaffected by either of
    this pass's new capabilities) ---
  - GL-002 "Journals must be posted by authorised users", GL-007
    "Journals above threshold require approval", PR-002 "Approval limits
    must be respected", PR-019 "High-value payments require additional
    approval": all four need journal_entries/purchase_orders/payments'
    prepared_by/created_by/paid_by (an employee_id) resolved to a user,
    then to that user's role via user_roles, then to that role's
    permission or approval_limits.max_amount — journal_entries/
    purchase_orders/payments, user, user_roles, approval_limits (or
    role_permissions, which for GL-002 doesn't even contain a matching
    permission value) is 4 real objects, one past what ThreeWayMatchRule
    supports. Detailed at length in 0052. All four still blocked.
  - PM-008 "Patch compliance must be reconciled per device": needs
    asset_register -> system_inventory (OS) -> patch_catalogue (patches
    for that OS) -> patch_inventory (installed?), a 4-object chain, ON TOP
    OF patch_inventory's composite (asset_id, patch_id) key having no
    single-column join_field that identifies "this asset's requirement for
    THIS specific patch" (see the no-unique-per-row-key entry below) — two
    independent reasons, either alone sufficient to block this. Still
    blocked.

--- Dynamic non-date/no-join-key lookup (a single scalar or list value
    that would have to be read dynamically from a config-style lookup
    table, either with no per-row join key to the primary object at all,
    or as a LIST rather than a number — a related but DIFFERENT gap from
    the dynamic-relative-date cluster this migration closes, which always
    compares a DATE to now() via a genuine per-row NUMERIC join-key
    lookup; ALSO considered and declined for the scalar cases:
    ParameterReference, which could technically supply a number here, but
    would silently substitute a platform-side org setting for what the
    client's own named config table is supposed to provide as ground
    truth — a materially different, and less honest, thing to template
    than converting an already-invented literal day-count to a tunable
    one, which is all 0055 ever did) ---
  - BK-002 "Backups must run according to schedule": see above — a
    free-text schedule category, not a numeric offset of any kind.
  - EN-004 "Weak encryption protocols must be identified": system_
    configurations (system_id) vs. security_baselines (a single generic
    "control"/"required_value" row, no system_id at all) — no join key of
    any kind, so this is not "dynamic lookup" so much as "no lookup path
    exists." A same-object ThresholdRule (tls_version < ParameterReference
    ("min_tls_version", default=1.2), no join at all) was considered and
    rejected: security_baselines is explicitly named in this control's own
    required_tables as the source of truth for what "approved" means, and
    required_value is real, client-editable data — substituting a
    platform-side constant would silently stop reading it. Still blocked.
  - RA-006 "Remote access outside approved hours/geography should be
    reviewed": access_policies.allowed_countries is a LIST value that
    would have to be read dynamically per policy row and used as the
    membership list for an "in"/"not_in" check — "in"/"not_in" only test a
    field against a FIXED literal list baked into the template at
    authoring time; DynamicRelativeDateComparison's offset is a single
    number read from a field, not a list, and does not generalise to this
    case. allowed_hours compounds this with a time-of-day (not date)
    window no primitive parses either. Still blocked.
  - RA-010 "Idle remote sessions must time out": session_policies has no
    session_id/user_id at all — no join key to remote_access_logs exists
    at any granularity, per-row or otherwise. The same ThresholdRule +
    ParameterReference substitution was considered for the same reason as
    EN-004 and declined for the same reason. Still blocked.
  - RA-012 "Split-tunnelling / unauthorised network bridging should be
    identified": vpn_config and network_policies are both singleton
    config tables with no shared field at all. Still blocked.

--- No value correspondence at all (confirmed no field on either side
    holds a value that corresponds to the other; unaffected by either of
    this pass's new capabilities, which both operate WITHIN an existing
    join, not in place of one) ---
  - AS-001 "IT assets must be recorded": asset_register (AST-prefixed) vs.
    system_inventory (SYS-prefixed) — different ID schemes.
  - NW-004 "Network devices must be inventoried": network_devices vs.
    asset_register — same class of gap.
  - OP-005 "System incidents must be logged": incidents has no "system"
    field, system_logs has no "incident_id" field.
  - PM-009 "Third-party/application patches must be tracked": software_
    inventory vs. patch_releases — nothing connects an installed software
    title to a specific patch release.
  - VM-003 "Unsupported systems must be identified": assets/software_
    inventory have no path to support_matrix at any hop.
  - WF-003 "Blocked access attempts must be logged": web_filter_logs vs.
    security_logs — no linking id at all.
  - WF-006 "Filtering must apply consistently on and off network": web_
    filter_status (asset-keyed) vs. remote_access_logs (user/session-
    keyed) — bridging would need 5 objects, past even the 4-object gap.
  - WF-009 "HTTPS/TLS inspection must be correctly configured": web_
    filter_config vs. certificates — no field marks which certificate
    belongs to which TLS-inspection config.

--- Aggregate/statistical (no primitive computes SUM/COUNT/AVG or a
    distinct-value count across rows, or a sequential period-over-period
    comparison; unaffected by either of this pass's new capabilities) ---
  - AC-008 "Shared accounts should be restricted": needs a DISTINCT count
    of person/IP per account, not a row-count duplicate check.
  - GL-009 "Debits and credits must balance": SUM(debit) vs SUM(credit)
    per journal_id.
  - GL-010 "GL should reconcile to subledger": multi-table SUM
    reconciliation.
  - PY-008 "Unusual salary increases should be investigated": needs a
    period-over-period (this row vs. the SAME employee's immediately PRIOR
    period) comparison; payroll_history has no unique per-row key beyond
    (employee_id, period), so a self-join on employee_id alone pairs every
    period against every OTHER period, not specifically the preceding one.
  - VM-001 "Vulnerabilities must be identified": describes a data-
    ingestion process, not a row-level condition.

--- Business-mapping / would invent an undefined client policy
    (unaffected by either of this pass's new capabilities) ---
  - CM-006 "Changes outside approved window require review": deployed_at
    (a timestamp) vs. change_requests.scheduled_window (a category like
    "weekend") — a category-to-time-window translation no operator here
    performs.
  - GL-006 "Unusual journals should be investigated": audit_procedure
    names no single concrete, testable condition.
  - NW-003 "Excessively permissive firewall rules should be identified":
    the seeded "violation" (port 3389 open to 0.0.0.0/0) is a specific
    business rule with no canonical representation.
  - OTC-008 "Bad debts should be monitored": receivables.days_overdue is
    real and usable, but no materiality/SLA threshold concept exists
    anywhere in the model for receivables aging.
  - WF-002 "Malicious/blacklisted categories must be blocked by policy":
    web_filter_policies.category <-> security_baselines.control is a
    business-name mapping, not a literal shared value or a regex-
    expressible relationship.

--- No unique per-row key (a safe self-join/dedup, or a single-column
    join_field, needs a genuine one-row-per-value key; unaffected by
    either of this pass's new capabilities) ---
  - PM-002 "Unpatched systems must be identified": patch_inventory's real
    key is the (asset_id, patch_id) PAIR — no single join_field expresses
    "this asset's requirement for this specific patch."
  - RA-002 "Remote access must be explicitly authorised": remote_access_
    grants has no per-grant id (only user_id/granted_at/approved) — a
    self-join or filter-only test on a non-unique key produces duplicated/
    fan-out results, not one exception per violating row.
  - (PM-008 also belongs here — see the 4-object-chain entry above, which
    it is blocked by independently as well.)

--- Missing-canonical-field gap considered and deliberately NOT filled
    (re-examined carefully per this pass's specific instruction to revisit
    this one) ---
  - SOD-005 "Employee creation and payroll processing should be
    segregated", SOD-006 "Customer creation and credit approval should be
    segregated": both need a creator/administrator field on a MASTER-DATA
    object (employee for SOD-005, customers for SOD-006). Re-examined
    against the same bar every other field addition in this project has
    had to clear — completing an existing, established pattern rather than
    inventing a new one — and it still doesn't clear it: EVERY master-data
    object in this model (employee, customers, suppliers) consistently
    carries no creator/administrator attribution anywhere, unlike
    supplier_invoices.processed_by (added in an earlier round), which
    filled a gap on a TRANSACTIONAL document type where every sibling
    object (purchase_orders.created_by, journal_entries.prepared_by)
    already carries the equivalent field. Adding one to employee/customers
    now, only to unblock these two controls, would be introducing a new
    attribution pattern for master data rather than completing one that
    already exists elsewhere in the model — the same discipline call
    0053 made, re-confirmed rather than revisited away. Both remain
    blocked.

--- Structurally deeper than list-membership alone ---
  - SOD-007 "Identify conflicting roles": needs a PAIRWISE dynamic
    comparison against sod_rules.conflicting_permissions (a 2-element list
    per rule) — does some role hold BOTH list[0] AND list[1] of the SAME
    sod_rules row. "in"/"not_in" test one field against a fixed literal
    list; they cannot express list-indexing combined with a self-join
    keyed off dynamic per-row list data. SOD-001 (0050) solved the one
    seed-data case this control's own conflict matrix defines by
    hardcoding the two literal permission values instead of reading them
    from sod_rules; templating SOD-007 the same way for its other row
    would just be a second, differently-coded copy of the same technique,
    and neither of that row's two permission values (create_purchase_order
    / approve_purchase_order) actually exists anywhere in role_permissions
    to test against, so even the hardcoded shortcut has no real data to
    produce a correct result from. Still blocked.

--- Still blocked, no capability applies ---
  - OP-003 "Critical jobs must run according to schedule": scheduled_
    jobs.schedule is a free-text cadence string ("daily 02:00"/"monthly"/
    "weekly") with no canonical numeric-window mapping — the same
    category-vs-numeric-offset problem as BK-002 above, and for the same
    reason not solved by DynamicRelativeDateComparison (which reads a
    NUMERIC offset field, not a free-text category). An existence-only
    version ("has this job EVER run") would pass the seed fixture's one
    violation but tests something materially narrower than "runs according
    to schedule" (ongoing cadence adherence) — rejected on the same
    discipline as GL-004's rejected shortcut. Still blocked.

47 = 10 templated (5 in 0059, 5 here) + 37 blocked. The 37: API-002,
RA-003, RA-007 (3) + GL-002, GL-007, PR-002, PR-019, PM-008 (5) + BK-002,
EN-004, RA-006, RA-010, RA-012 (5) + AS-001, NW-004, OP-005, PM-009,
VM-003, WF-003, WF-006, WF-009 (8) + AC-008, GL-009, GL-010, PY-008,
VM-001 (5) + CM-006, GL-006, NW-003, OTC-008, WF-002 (5) + PM-002, RA-002
(2) + SOD-005, SOD-006 (2) + SOD-007 (1) + OP-003 (1) =
3+5+5+8+5+5+2+2+1+1 = 37. 10 + 37 = 47. Every one of the 47 is accounted
for exactly once above (PM-008 is discussed under both the 4-object-chain
and no-unique-per-row-key headings, since it is independently blocked by
both reasons, but it is one control and is counted only once in the 37).

Final coverage: 120/157.

Revision ID: 0060
Revises: 0059
Create Date: 2026-09-17
"""
import json

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None

_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "BK-004",
        "Backup older than its system's retention window",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "backup_jobs",
            "secondary_object": "backup_retention_rules",
            "join_field": "system_id",
            "condition_primary": {"field": "system_id", "operator": "is_not_null"},
            "condition_secondary": {"field": "system_id", "operator": "is_not_null"},
            "dynamic_relative_date_comparison": {
                "primary_field": "run_at",
                "operator": "lt",
                "secondary_field": "retention_days",
                "direction": -1,
            },
        },
    ),
    (
        "OP-007",
        "System log older than its system's retention window",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "system_logs",
            "secondary_object": "log_retention_rules",
            "join_field": "system",
            "condition_primary": {"field": "system", "operator": "is_not_null"},
            "condition_secondary": {"field": "system", "operator": "is_not_null"},
            "dynamic_relative_date_comparison": {
                "primary_field": "logged_at",
                "operator": "lt",
                "secondary_field": "retention_days",
                "direction": -1,
            },
        },
    ),
    (
        "DP-004",
        "Data record older than its dataset's retention window",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "data_records",
            "secondary_object": "retention_rules",
            "join_field": "dataset",
            "condition_primary": {"field": "dataset", "operator": "is_not_null"},
            "condition_secondary": {"field": "dataset", "operator": "is_not_null"},
            "dynamic_relative_date_comparison": {
                "primary_field": "created_at",
                "operator": "lt",
                "secondary_field": "retention_days",
                "direction": -1,
            },
        },
    ),
    (
        "PM-001",
        "Critical patch not installed beyond its SLA window",
        {
            "rule_type": "three_way_match",
            "primary_object": "patch_inventory",
            "secondary_object": "patch_releases",
            "tertiary_object": "sla_rules",
            "join_field_primary_secondary": "patch_id",
            "join_field_secondary_tertiary": "severity",
            "condition_primary": {"field": "installed", "operator": "eq", "value": False},
            "condition_tertiary": {"field": "patch_within_days", "operator": "is_not_null"},
            "dynamic_relative_date_comparison": {
                "date_object": "secondary",
                "date_field": "released_at",
                "operator": "lt",
                "offset_object": "tertiary",
                "offset_field": "patch_within_days",
                "direction": -1,
            },
        },
    ),
    (
        "VM-002",
        "Open critical vulnerability beyond its SLA remediation window",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "vulnerabilities",
            "secondary_object": "sla_rules",
            "join_field": "severity",
            "condition_primary": {"field": "status", "operator": "eq", "value": "open"},
            "condition_secondary": {"field": "remediate_within_days", "operator": "is_not_null"},
            "dynamic_relative_date_comparison": {
                "primary_field": "discovered_at",
                "operator": "lt",
                "secondary_field": "remediate_within_days",
                "direction": -1,
            },
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
