"""Extend rule-template coverage: Master Data Management, Order-to-Cash,
Change Management (remaining), IT Operations (remaining), IT Asset
Management, and Backup & Recovery.

Same validation discipline as 0044/0046: every rule_definition here was
schema-validated against app.schemas.test_rule AND dry-run against a copy
of the Gateway rule engine with synthetic good/bad rows. See 0046's
docstring for the two reusable techniques ("boolean self-filter",
"reused cross-object rule") applied again below.

A third recurring judgment call in this migration, worth naming: several
controls have a canonical field on the PRIMARY object that would let a
plain missing_match be scoped to a meaningful subset (e.g. system.
criticality, data_assets.sensitivity, privacy_requests.type). Wherever such
a field exists, an UNFILTERED missing_match across the whole table would
flag rows the control was never meant to cover — that's the "filtered
missing-match" gap named throughout these migrations, and those controls
are skipped, not approximated. Conversely, several other controls (e.g.
OTC-004, OTC-007, MD-001) have NO such distinguishing field anywhere in the
canonical model for that object — there, an unfiltered missing_match IS the
correct, complete test (every row genuinely is in scope), not a downgraded
approximation of a better rule. Each control below is scoped one way or the
other for a stated reason, not by default.

=== Templated in this migration ===
Master Data Management:
  - MD-001 "Supplier creation requires approval": missing_match, suppliers
    -> supplier_approvals on supplier_id.
  - MD-002 "Duplicate suppliers should be prevented": duplicate, suppliers,
    group_by tax_number.
  - MD-003 "Supplier bank changes require approval": boolean self-filter —
    supplier_bank_changes already carries its own approved_by field
    directly; flagged via is_null on a self-join keyed by change_id (a
    genuine per-row key), rather than joining to the generic `approvals`
    table (which only has a free-text `reference` field, not a literal
    change_id column to join on).
  - MD-004 "Inactive suppliers should not receive payments": same
    cross_match_condition shape as the already-templated PR-016 / this
    migration's PR-020 (payments -> suppliers, supplier_id, inactive).
  - MD-006 "Customer creation requires approval": missing_match, customers
    -> customer_approvals on customer_id.
  - MD-007 "Duplicate customers should be identified": duplicate,
    customers, group_by tax_number.
  - MD-008 "Credit limits require approval": missing_match, customers ->
    credit_approvals on customer_id.
  - MD-009 "Inactive customers should not transact": cross_match_condition,
    sales_orders -> customers on customer_id, customer status == inactive.
Order-to-Cash:
  - OTC-001 "Sales orders require approval": missing_match, sales_orders ->
    sales_approvals on order_id.
  - OTC-002 "Customers must be valid": missing_match, sales_orders ->
    customers on customer_id (flags orders referencing a customer_id with
    no master record at all).
  - OTC-004 "Invoice should follow delivery": missing_match, sales_invoices
    -> deliveries on invoice_id.
  - OTC-005 "Duplicate invoices should be prevented": duplicate,
    sales_invoices, group_by invoice_id.
  - OTC-007 "Receipts should be allocated": missing_match, customer_receipts
    -> sales_invoices on invoice_id.
IT Asset Management:
  - AS-004 "Unauthorised software should be identified": missing_match,
    software_inventory -> approved_software on software_name (the one pair
    of tables in this domain that actually share a literal field name;
    every other AS control's two required tables use differently-named ID
    fields — see the skip list).
Backup & Recovery:
  - BK-003 "Failed backups must be investigated": boolean self-filter —
    backup_jobs.status == "failed", self-joined on backup_id (a genuine
    per-row key). Deliberately scoped to just "the backup failed," not
    "...and no incident was logged" (that half needs a filtered
    missing-match — see below).
  - BK-005 "Recovery testing must occur": missing_match, system ->
    recovery_tests on system_id. Checked against the seed fixture before
    committing to leaving this unfiltered: SYS003 (criticality "medium",
    not "critical") is explicitly staged as a genuine BK-005 violation
    alongside SYS004 ("critical"), confirming this control is NOT meant to
    be criticality-scoped the way BK-001 is (see below) — every system
    needs a recovery test on file, regardless of tier. Deliberately scoped
    to "never tested," not "tested too long ago" (that half needs a
    relative-date window).
  - BK-006 "Recovery tests must pass": threshold, recovery_tests.result ==
    "fail" (a plain categorical value, not a magnitude).

=== Deliberately NOT templated, with the specific gap ===

Master Data Management (MD-005):
  - MD-005 "Supplier master changes must be logged": audit_logs uses a
    polymorphic entity_type/entity_id pair; suppliers has no "entity_id"
    field, so there's no shared join_field name (same gap as PY-010/DP-002
    below).

Order-to-Cash (OTC-003, OTC-006, OTC-008):
  - OTC-003 "Credit limits must be respected": needs a field-to-field
    comparison (sales_orders.amount vs customers.credit_limit) — the same
    gap as the P2P/SOD field-to-field controls in 0046.
  - OTC-006 "Discounts require approval": sales_orders has no discount-
    amount (or has-discount) field at all — discount_approvals is the ONLY
    place a discount amount is recorded, so there is no way to identify,
    from sales_orders, which orders even had a discount requiring
    approval in the first place. A missing_match from sales_orders would
    flag every order with zero discount too — a genuine missing-canonical-
    field gap, not a primitive gap.
  - OTC-008 "Bad debts should be monitored": receivables.days_overdue is a
    real, usable field, but no canonical field anywhere defines what
    "overdue" threshold makes a balance a monitoring concern (no SLA/
    materiality table exists for receivables aging, unlike e.g.
    vulnerabilities' sla_rules). Picking an arbitrary day-count would be
    inventing a client-specific materiality policy that isn't in the data
    — the same discipline that kept 0044 from ever using ThresholdRule
    with a magnitude literal.

Change Management (CM-003, CM-004, CM-006):
  - CM-003 "Emergency changes require retrospective approval": this
    control's own required_tables are production_changes + change_
    approvals, but the "emergency" signal only exists on change_requests.
    scheduled_window (== "emergency" in the seed) — a table CM-003 doesn't
    even declare. Even allowing that substitution, the real test is a
    *filtered* missing-match (only emergency-flagged changes need this
    check) — cross_match_condition can only test for existence of a match,
    not absence of one, so this needs both a table substitution and a
    filtered anti-join, neither of which the current primitives provide.
  - CM-004 "Developer/deployer segregation": needs a field-to-field
    comparison (code_changes.committed_by vs deployments.deployed_by on
    the same change_id) — same gap as SOD-001..006.
  - CM-006 "Changes outside approved window require review": needs a
    time-window range comparison (deployed_at against change_requests.
    scheduled_window, e.g. "weekend" vs an actual timestamp) — not a
    literal-value comparison.

IT Operations (OP-002, OP-003, OP-004, OP-005, OP-007):
  - OP-002 "Failed jobs must be investigated": needs a filtered
    missing-match (only job_executions with status == "failed" need a
    matching incident_records row; an unfiltered join would demand an
    incident for every successful run too).
  - OP-003 "Critical jobs must run according to schedule": needs a
    relative-date/schedule-recency window.
  - OP-004 "Processing time should remain within limits": job_executions.
    duration_seconds is real and usable, but no canonical field defines a
    per-job (or global) limit anywhere — scheduled_jobs has no max-
    duration field, so any cutoff would be an invented magic number, the
    same discipline call as OTC-008.
  - OP-005 "System incidents must be logged": incidents has no "system"
    field and system_logs has no "incident_id" field — no shared
    join_field name exists between the two objects at all.
  - OP-007 "Logs must be retained": needs a relative-date window
    (logged_at + log_retention_rules.retention_days vs "now").

IT Asset Management (AS-001, AS-002, AS-003, AS-005):
  - AS-001 "IT assets must be recorded": asset_register's key is asset_id;
    system_inventory's is system_id — different field names, so
    missing_match (which requires the identical field name on both
    objects) cannot join them.
  - AS-002 "Assigned assets must belong to valid employees": asset_
    register.assigned_to vs employee.employee_id — different field names,
    same join-alias gap as AS-001.
  - AS-003 "Terminated employees must return assets": same assigned_to/
    employee_id field-name mismatch as AS-002, compounded by needing a
    cross_match_condition that AS-002's join gap already blocks.
  - AS-005 "Unsupported systems should be identified": needs a relative-
    date comparison (support_matrix.supported_until vs "now") — a plain
    missing_match version (system_inventory.os not present at all in
    support_matrix) would test a materially different and narrower thing
    than "past its support end date," which is the actual, common case
    this control cares about.

Backup & Recovery (BK-001, BK-002, BK-004):
  - BK-001 "Critical systems must be backed up": needs a filtered
    missing-match — system.criticality exists specifically to scope this
    ("critical" systems only), so an unfiltered join would demand a
    backup_jobs row for every system regardless of tier, which is not
    what the control asks for.
  - BK-002 "Backups must run according to schedule": needs a relative-date
    comparison (backup_jobs.run_at recency vs backup_schedules.frequency).
  - BK-004 "Backup retention requirements must be met": needs a
    relative-date window (run_at + backup_retention_rules.retention_days
    vs "now").

Revision ID: 0047
Revises: 0046
Create Date: 2026-09-15
"""
import json

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "MD-001",
        "Supplier has no recorded creation approval",
        {"rule_type": "missing_match", "primary_object": "suppliers", "secondary_object": "supplier_approvals", "join_field": "supplier_id"},
    ),
    (
        "MD-002",
        "Duplicate supplier tax number",
        {"rule_type": "duplicate", "object": "suppliers", "group_by": ["tax_number"]},
    ),
    (
        "MD-003",
        "Supplier bank account change has no recorded approver",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "supplier_bank_changes",
            "secondary_object": "supplier_bank_changes",
            "join_field": "change_id",
            "condition_primary": {"field": "approved_by", "operator": "is_null"},
            "condition_secondary": {"field": "change_id", "operator": "is_not_null"},
        },
    ),
    (
        "MD-004",
        "Payment made to an inactive supplier",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "payments",
            "secondary_object": "suppliers",
            "join_field": "supplier_id",
            "condition_primary": {"field": "payment_id", "operator": "is_not_null"},
            "condition_secondary": {"field": "status", "operator": "eq", "value": "inactive"},
        },
    ),
    (
        "MD-006",
        "Customer has no recorded creation approval",
        {"rule_type": "missing_match", "primary_object": "customers", "secondary_object": "customer_approvals", "join_field": "customer_id"},
    ),
    (
        "MD-007",
        "Duplicate customer tax number",
        {"rule_type": "duplicate", "object": "customers", "group_by": ["tax_number"]},
    ),
    (
        "MD-008",
        "Customer credit limit has no recorded approval",
        {"rule_type": "missing_match", "primary_object": "customers", "secondary_object": "credit_approvals", "join_field": "customer_id"},
    ),
    (
        "MD-009",
        "Sales order placed for an inactive customer",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "sales_orders",
            "secondary_object": "customers",
            "join_field": "customer_id",
            "condition_primary": {"field": "order_id", "operator": "is_not_null"},
            "condition_secondary": {"field": "status", "operator": "eq", "value": "inactive"},
        },
    ),
    (
        "OTC-001",
        "Sales order has no recorded approval",
        {"rule_type": "missing_match", "primary_object": "sales_orders", "secondary_object": "sales_approvals", "join_field": "order_id"},
    ),
    (
        "OTC-002",
        "Sales order references an unknown customer",
        {"rule_type": "missing_match", "primary_object": "sales_orders", "secondary_object": "customers", "join_field": "customer_id"},
    ),
    (
        "OTC-004",
        "Sales invoice has no matching delivery",
        {"rule_type": "missing_match", "primary_object": "sales_invoices", "secondary_object": "deliveries", "join_field": "invoice_id"},
    ),
    (
        "OTC-005",
        "Duplicate sales invoice",
        {"rule_type": "duplicate", "object": "sales_invoices", "group_by": ["invoice_id"]},
    ),
    (
        "OTC-007",
        "Customer receipt is not allocated to an invoice",
        {"rule_type": "missing_match", "primary_object": "customer_receipts", "secondary_object": "sales_invoices", "join_field": "invoice_id"},
    ),
    (
        "AS-004",
        "Installed software is not on the approved list",
        {"rule_type": "missing_match", "primary_object": "software_inventory", "secondary_object": "approved_software", "join_field": "software_name"},
    ),
    (
        "BK-003",
        "Backup job failed",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "backup_jobs",
            "secondary_object": "backup_jobs",
            "join_field": "backup_id",
            "condition_primary": {"field": "status", "operator": "eq", "value": "failed"},
            "condition_secondary": {"field": "backup_id", "operator": "is_not_null"},
        },
    ),
    (
        "BK-005",
        "System has never had a recovery test on file",
        {"rule_type": "missing_match", "primary_object": "system", "secondary_object": "recovery_tests", "join_field": "system_id"},
    ),
    (
        "BK-006",
        "Recovery test failed",
        {"rule_type": "threshold", "object": "recovery_tests", "field": "result", "operator": "eq", "value": "fail"},
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
