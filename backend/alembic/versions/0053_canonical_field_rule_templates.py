"""Extend rule-template coverage using new canonical_model.py fields — the
second of three migrations completing the third and final rule-template
pass (0052-0054). Unlike every prior pass, this one is explicitly
authorized to ADD fields to existing canonical objects (never rename,
remove, or add whole new objects) when a control genuinely cannot be
expressed without an attribute that should obviously exist on an object
already in the model. All six additions below were made directly in
app/core/canonical_model.py as their own reviewable change, each with an
inline comment explaining the specific control it unblocks and why the
field is a natural, standard attribute rather than invented business logic
or a fabricated cross-system link. This migration is what actually turns
each addition into a working template.

Same validation discipline as every prior rule-template migration: every
rule_definition here was schema-validated against app.schemas.test_rule's
Pydantic models AND dry-run against BOTH engines (app.services.
rule_evaluation and gateway.gateway.rule_engine) with synthetic good/bad
rows, confirming each produces exactly the expected exception set in both.

=== The line this migration draws: attribute vs. fabricated structure ===
Every addition below fills in a MISSING ATTRIBUTE on an object that
already, obviously, has business reasons to carry it — several because a
clearly analogous sibling object in the SAME domain already has the
equivalent field (patch_exceptions vs. web_filter_exceptions' exception_id;
supplier_invoices vs. purchase_orders' created_by / journal_entries'
prepared_by). None of them invent a cross-system LINK between two objects
that have no real relationship in a client's actual schema — that
distinction is exactly why AS-001 (asset_register vs. system_inventory,
different ID schemes entirely) and NW-004 (network_devices vs.
asset_register, no shared concept at all) are NOT "fixed" with a field
addition here, and remain blocked below and in 0054: manufacturing a join
key between two genuinely separate entity types would misrepresent what a
real client's data model looks like, which is a different and worse
mistake than an attribute gap.

=== New canonical_model.py fields (see the file itself for the full
    inline reasoning on each) ===
  - general_ledger.account_type — GL-008.
  - sales_orders.discount_amount — OTC-006.
  - scheduled_jobs.max_duration_seconds — OP-004.
  - patch_exceptions.exception_id — PM-010.
  - remote_access_logs.outcome — RA-011.
  - supplier_invoices.processed_by — SOD-003.

=== Templated in this migration ===

Financial / General Ledger:
  - GL-008 "Suspense accounts require review": cross_match_condition,
    self-join on general_ledger.account (a genuine per-row key — one row
    per account), condition_primary account_type == "suspense",
    condition_secondary balance != 0. Deliberately requires BOTH
    conditions, not just the classification alone: a suspense account
    that has already cleared to a zero balance is resolved, not a review
    exception — "unresolved" is the operative word in the control's own
    wording, and account_type == "suspense" alone would flag it anyway.

IT Operations:
  - OP-004 "Processing time should remain within limits": cross_match_
    condition, job_executions <-> scheduled_jobs on job_id (a shared field
    name), field_comparison duration_seconds > max_duration_seconds. Now a
    genuine dynamic per-job comparison instead of an invented global
    cutoff — the exact discipline 0044/0049/0051 held to in rejecting a
    hardcoded number here.

Order-to-Cash:
  - OTC-006 "Discounts require approval": missing_match, sales_orders ->
    discount_approvals on order_id, primary_condition discount_amount > 0.
    Scoped to orders that actually carry a discount — an unfiltered
    version would demand a discount_approvals row for every order, which
    0047/0050 already correctly rejected.

Patch Management:
  - PM-010 "Patch exceptions/deferrals require documented justification":
    missing_match, patch_exceptions -> exception_approvals on
    exception_id, now a literal shared field name.

Remote Access:
  - RA-011 "Failed remote login attempts must be monitored": threshold,
    remote_access_logs.outcome == "failed". Deliberately scoped to
    remote_access_logs only (the control's own required_tables lists
    login_history too, but login_history has no equivalent outcome field
    and adding one there as well was judged unnecessary — remote_access_
    logs already gives session-level failed-login visibility, which is
    the more specific and more directly relevant of the two tables to
    this control's "remote" wording).

Segregation of Duties:
  - SOD-003 "Invoice creation and payment should be segregated": cross_
    match_condition, supplier_invoices <-> payments on invoice_number (a
    shared field name), field_comparison processed_by == paid_by
    (pass-through conditions both sides — the same technique 0050
    established for SOD-002/SOD-004/PR-008/PR-009/PR-012/PR-013/PR-018/
    GL-004/OTC-003/CM-004).

=== Field additions considered and deliberately REJECTED, with why ===

  - SOD-005 "Employee creation and payroll processing should be
    segregated" and SOD-006 "Customer creation and credit approval should
    be segregated" both need a creator/administrator field on a MASTER-
    DATA object (employee for SOD-005, customers for SOD-006) that the
    canonical model consistently does NOT carry anywhere — suppliers, the
    other master-data object in the model, also has no creator field.
    Adding one just for SOD-005/SOD-006 would be introducing a new pattern
    (attributing master-data record creation to a specific actor) rather
    than completing an existing one, unlike supplier_invoices.processed_by
    above, which fills a gap on a TRANSACTIONAL document type where every
    sibling object (purchase_orders, journal_entries) already carries the
    equivalent field. Both remain blocked; see 0054's final accounting.
  - AS-001/NW-004: considered and rejected — see the module docstring's
    "attribute vs. fabricated structure" section above. Both remain
    blocked; see 0054's final accounting.
  - OTC-008 "Bad debts should be monitored": receivables.days_overdue is
    real and usable, but no materiality/SLA threshold concept exists
    anywhere in the model for receivables aging (unlike vulnerabilities'
    sla_rules). Adding a hardcoded day-count field would be inventing an
    undefined client policy value, not filling a structural gap — the same
    discipline call that has rejected NW-003/OP-004(pre-this-migration)/
    GL-006 throughout this project. Remains blocked; see 0054.
  - DP-004 "Data retention requirements must be followed": adding a
    "dataset" field to data_records would fix only one of its two
    compounding gaps — the other (retention_rules.retention_days is a
    per-dataset DYNAMIC offset, and no capability in any of the three
    rounds lets a rule compare a date field against "now minus a value
    looked up from another table's row," as opposed to a fixed
    RelativeDate baked in at template-authoring time) would still block it
    regardless. Adding the field without also having the capability gap it
    would unblock serves no purpose, so it was not made. Remains blocked;
    see 0054, which names this "dynamic relative-date" gap precisely and
    flags it as a real, recurring, unaddressed engine gap.

Revision ID: 0053
Revises: 0052
Create Date: 2026-09-16
"""
import json

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "GL-008",
        "Suspense account has an unresolved (nonzero) balance",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "general_ledger",
            "secondary_object": "general_ledger",
            "join_field": "account",
            "condition_primary": {"field": "account_type", "operator": "eq", "value": "suspense"},
            "condition_secondary": {"field": "balance", "operator": "ne", "value": 0},
        },
    ),
    (
        "OP-004",
        "Job execution duration exceeded its configured maximum",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "job_executions",
            "secondary_object": "scheduled_jobs",
            "join_field": "job_id",
            "condition_primary": {"field": "job_id", "operator": "is_not_null"},
            "condition_secondary": {"field": "job_id", "operator": "is_not_null"},
            "field_comparison": {"primary_field": "duration_seconds", "operator": "gt", "secondary_field": "max_duration_seconds"},
        },
    ),
    (
        "OTC-006",
        "Discounted sales order has no recorded discount approval",
        {
            "rule_type": "missing_match",
            "primary_object": "sales_orders",
            "secondary_object": "discount_approvals",
            "join_field": "order_id",
            "primary_condition": {"field": "discount_amount", "operator": "gt", "value": 0},
        },
    ),
    (
        "PM-010",
        "Patch exception has no recorded approval",
        {"rule_type": "missing_match", "primary_object": "patch_exceptions", "secondary_object": "exception_approvals", "join_field": "exception_id"},
    ),
    (
        "RA-011",
        "Remote access login attempt failed",
        {"rule_type": "threshold", "object": "remote_access_logs", "field": "outcome", "operator": "eq", "value": "failed"},
    ),
    (
        "SOD-003",
        "Same person processed the supplier invoice and made the payment",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "supplier_invoices",
            "secondary_object": "payments",
            "join_field": "invoice_number",
            "condition_primary": {"field": "invoice_number", "operator": "is_not_null"},
            "condition_secondary": {"field": "invoice_number", "operator": "is_not_null"},
            "field_comparison": {"primary_field": "processed_by", "operator": "eq", "secondary_field": "paid_by"},
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
