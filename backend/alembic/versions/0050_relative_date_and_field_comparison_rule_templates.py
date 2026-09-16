"""Extend rule-template coverage using the 4 rule-engine capabilities added
since migration 0049 (RelativeDate, CrossMatchConditionRule.field_comparison,
MissingMatchRule.primary_condition, and secondary_join_field on both
missing_match and cross_match_condition) — see app.schemas.test_rule's
module docstring for their exact semantics.

Domains in this migration: User Access Management, Segregation of Duties,
Procure-to-Pay, Financial/GL, Payroll, Master Data Management, Order-to-
Cash, Change Management (i.e. the first half of the 20-domain library,
matching 0044/0046's domain split). The second half (IT Operations through
Remote Access) is migration 0051.

Same validation discipline as every prior rule-template migration: every
rule_definition here was schema-validated against app.schemas.test_rule's
Pydantic models AND dry-run against BOTH engines (app.services.
rule_evaluation, the backend's pandas-free mirror, and gateway.gateway.
rule_engine, the pandas original) with synthetic good/bad rows, confirming
each produces exactly the expected exception set in both — not just "it
parses." Every canonical object/field name used below is taken verbatim
from app/core/canonical_model.py.

This migration re-derives every control from the 89-control gap catalogue
(built after 0049) that the previous report tagged as blocked specifically
on relative-date, field-to-field comparison, filtered missing-match, or a
join-field-name mismatch — the four categories the new capabilities target.
Each control below was independently re-checked against its actual
audit_procedure and canonical_model.py fields, NOT assumed correct from the
prior one-line category guess. Several controls in that catalogue turned
out to still be blocked even with the new capability, usually because a
SECOND, different gap was compounding the first (the catalogue itself
warned this could happen) — those are listed in their own section below
with the specific remaining reason, not silently dropped.

=== New technique: self-join with field_comparison ===
CrossMatchConditionRule.field_comparison lets a cross_match_condition
compare a field on the primary side to a field on the secondary side
post-join, instead of only to a literal. The most common shape below is a
plain two-object join (e.g. supplier_invoices vs purchase_orders on
po_number) where BOTH condition_primary and condition_secondary are
harmless pass-through conditions (is_not_null on the join key itself, or
an equivalent always-true field — same "pass-through condition" pattern
0044 established) and the real test lives entirely in field_comparison.

=== New technique: self-join on a non-unique key (SOD-001) ===
SOD-001 does NOT need field_comparison at all, despite being catalogued
alongside the other SOD creator/approver controls. Its real audit_procedure
("Identify users able to create suppliers and make payments") is a
permission-conflict test, not a same-record creator/approver test — it
needs to find a ROLE that holds two DIFFERENT, specific permission values,
each recorded as its OWN row in role_permissions. That is exactly the
existing self-join pattern (0044's OP-006 / 0049's PM-006: two independent
conditions, joined back on a shared key), except the join key here
(`role`) is deliberately NOT unique per row — role_permissions has exactly
one row per (role, permission) pair, so filtering to permission ==
"create_supplier" on one side and permission == "approve_payment" on the
other, then joining on role, correctly finds "ap_manager" without
multiplying rows the way self-joining a genuinely repeating key (like
patch_inventory's asset_id, 0049) would. This was already expressible
before any of the 4 new capabilities existed; the prior report
mischaracterized it by lumping it in with SOD-002..006 without checking
its actual required_tables/audit_procedure, which name a completely
different shape (role_permissions, not a creator/approver field pair on
one record). Corrected here per this migration's re-derivation mandate.
The result is a ROLE-level finding ("this role holds both permissions"),
not a per-user finding — a coarser but real and actionable exception,
consistent with how role_permissions is the only place these permissions
are actually recorded in the canonical model.

=== Partial-but-honest scoping (continuing 0048's EN-002 precedent) ===
PY-003 and GL-004 below are full fixes of the gap the prior report named.
No control in this migration needed partial scoping the way EN-002/RA-008
(0051) did — every PR/CM/OTC/GL field_comparison control here has both a
clean shared (or aliasable) join field and a directly comparable field
pair, so the fix is complete for the specific condition each control's
audit_procedure names.

=== Templated in this migration ===

User Access Management:
  - AC-003 "Dormant accounts must be disabled": cross_match_condition
    self-join on user, join_field user_id — condition_primary status ==
    "active", condition_secondary last_login <= 180 days ago (RelativeDate).
    180 days matches the seed fixture's own "dormant, no login in 180+
    days" comment for U006.
  - AC-006 "Periodic access reviews must occur": threshold,
    access_reviews.reviewed_at <= 180 days ago. This flags a STALE review
    on file (the seed's REV002, reviewed 400 days ago) — re-deriving this
    control confirmed the 0046 finding that the real, seeded violation is
    staleness, not absence. A user with NO access_reviews row at all
    remains unflagged: that would need combining missing_match's anti-join
    with this staleness filter in a single rule (an OR-of-two-primitives
    capability), which does not exist. Honestly partial, matching this
    migration's established precedent for exactly this shape of gap.

Segregation of Duties:
  - SOD-001 "Supplier creation and payment should be segregated":
    cross_match_condition self-join on role_permissions — see the
    technique note above. Flags a role holding both create_supplier and
    approve_payment.
  - SOD-002 "PO creation and approval should be segregated":
    cross_match_condition, purchase_orders <-> po_approvals on po_number,
    field_comparison created_by == approved_by (pass-through conditions
    both sides).
  - SOD-004 "Journal preparation and approval should be segregated":
    same shape as SOD-002, journal_entries <-> journal_approvals on
    journal_id, field_comparison prepared_by == approved_by.

Procure-to-Pay:
  - PR-008 "Invoice should match PO": cross_match_condition,
    supplier_invoices <-> purchase_orders on po_number, field_comparison
    amount != amount.
  - PR-013 "Invoice price should agree to PO price": same rule shape as
    PR-008 — a different control code testing the identical real condition
    (both audit_procedures reduce to the same invoice-amount-vs-PO-amount
    comparison against the same two tables), the "reused cross-object
    rule" pattern from 0046.
  - PR-009 "Invoice should match goods received": cross_match_condition,
    supplier_invoices <-> goods_receipts on po_number (shared field name
    on both), field_comparison quantity != received_quantity.
  - PR-012 "Invoice quantity should not exceed receipt": same tables/join
    as PR-009, but a directional field_comparison (quantity > received_
    quantity) matching this control's stricter, one-directional wording.
  - PR-018 "Payment amount should agree to invoice": cross_match_condition,
    payments <-> supplier_invoices on invoice_number (shared field name),
    field_comparison amount != amount.

Financial / General Ledger:
  - GL-004 "Journals posted after period-end require review":
    cross_match_condition, journal_entries <-> accounting_periods on
    period (shared field name), field_comparison posted_at > end_date.
    0046 rejected a DIFFERENT shortcut for this control (joining on period
    and checking period.status == "closed", which wrongly flagged every
    once-timely posting once its period later closed) — field_comparison
    now makes the CORRECT test possible directly: compare the journal's
    own posted_at against that period's actual end_date, which doesn't
    drift the way a status flag does.

Payroll:
  - PY-003 "Employee additions require approval": missing_match,
    employee -> employee_approvals on employee_id, primary_condition
    hire_date >= 550 days ago. 0046 rejected an unfiltered version of this
    (83% of a normal, long-tenured workforce has no employee_approvals row
    at all); primary_condition now scopes it to recent hires only, per
    0046's own "hired within the last N days" framing. 550 days was chosen
    to include the seed fixture's own named recent-hire violations
    (EMP006 at 500 days, EMP010 at 300 days) while excluding its long-
    tenured population (EMP003 at 600+ days).

Master Data Management:
  - (none — MD-005 is the domain's only untemplated control and remains
    blocked; see below.)

Order-to-Cash:
  - OTC-003 "Credit limits must be respected": cross_match_condition,
    sales_orders <-> customers on customer_id, field_comparison amount >
    credit_limit.

Change Management:
  - CM-003 "Emergency changes require retrospective approval":
    missing_match, change_requests -> change_approvals on change_id,
    primary_condition scheduled_window == "emergency". Reuses the table
    substitution 0047 already justified (CM-003's own required_tables list
    production_changes + change_approvals, but the "emergency" signal only
    exists on change_requests.scheduled_window).
  - CM-004 "Developer/deployer segregation": cross_match_condition,
    code_changes <-> deployments on change_id, field_comparison
    committed_by == deployed_by.

=== Deliberately NOT templated in this migration, with the specific
    remaining reason ===

User Access Management (AC-001, AC-005, AC-008, AC-009):
  - AC-001 "User access must be approved": still blocked. primary_condition
    scopes the PRIMARY side (active users) correctly, but the real test
    also needs the SECONDARY side filtered to status == "approved" — an
    access_request that exists but is still "pending" (the seed's own
    ARQ003/U006 case) must NOT count as a match, and MissingMatchRule has
    no secondary-side filter, only primary_condition. A genuinely different,
    still-missing capability from the one that unblocked CM-003/OP-002/
    EN-001/DP-005/BK-001 below (0051) — those controls' secondary object
    has no status-like field to worry about, so existence alone is
    correct; AC-001's does.
  - AC-005: unchanged from 0044 — needs a 3-way join (user_roles -> roles
    -> access_requests) plus a still-missing "is this role privileged" flag.
  - AC-008: unchanged from 0044 — needs a distinct-count aggregate, not a
    row-count duplicate check.
  - AC-009: unchanged from 0044 — needs regex/string-pattern matching.

Segregation of Duties (SOD-003, SOD-005, SOD-006, SOD-007):
  - SOD-003 "Invoice creation and payment should be segregated": re-
    deriving this against canonical_model.py shows a deeper gap than
    field-to-field comparison — supplier_invoices has NO creator/
    processed-by field at all (only invoice_number, supplier_id, po_number,
    amount, quantity, received_at), so there is nothing on the primary
    side to compare against payments.paid_by in the first place. A
    genuine missing-canonical-field gap, not a field_comparison gap.
  - SOD-005 "Employee creation and payroll processing should be
    segregated": same class of gap — neither employee (no "administered_
    by"/"created_by" field) nor payroll (no "processed_by" field) carries
    the field this control needs.
  - SOD-006 "Customer creation and credit approval should be segregated":
    same gap again — customers has no creator field (credit_approvals has
    approved_by, but there is nothing on customers to compare it against).
  - SOD-007: unchanged from 0044 — needs a multi-way join (user ->
    user_roles -> role_permissions, twice) plus a list-membership test
    against sod_rules.conflicting_permissions — neither exists.

Procure-to-Pay (PR-002, PR-011, PR-014, PR-019):
  - PR-002, PR-019: unchanged — need a dynamic per-role/per-tier threshold
    looked up from approval_limits; field_comparison compares two columns
    on already-joined rows, it does not resolve "the limit for THIS row's
    approver's role" as a value to compare against.
  - PR-011: unchanged — needs a genuine 3-way join (PO + GRN + invoice).
  - PR-014: unchanged — payments has no po_number field and goods_receipts
    has no invoice_number field, so there is still no single join_field
    connecting them even before the 3-way nature of the real test.

Financial / General Ledger (GL-002, GL-006, GL-007, GL-008, GL-009,
GL-010):
  - GL-002: unchanged — needs a permission-set lookup (prepared_by -> user
    -> user_roles -> role_permissions), a 3+-way join.
  - GL-006 "Unusual journals should be investigated": not previously
    re-stated in the post-0049 gap catalogue's summary list, but still
    blocked for the same reason 0047 gave it — the audit_procedure itself
    ("apply amount/date/account/user rules") names no single concrete,
    testable condition. None of the 4 new capabilities help when there is
    no condition to bind them to.
  - GL-007: unchanged — same dynamic per-role threshold gap as PR-002/
    PR-019 (approval_limits, keyed by role).
  - GL-008: unchanged — general_ledger has no account_type/classification
    field, only free-text account_name.
  - GL-009, GL-010: unchanged — need SUM(debit) vs SUM(credit) and
    multi-table aggregate reconciliation respectively; no primitive
    aggregates.

Payroll (PY-008, PY-010):
  - PY-008: unchanged — needs a period-over-period (this period vs the
    PRIOR period for the same employee) row-vs-row comparison; no
    primitive compares a field to another row's value of that same field.
  - PY-010 "Payroll changes should be logged": re-checked specifically per
    this migration's mandate to verify whether audit_logs's polymorphic
    structure "resolves cleanly with a plain asymmetric join" now that
    secondary_join_field exists. It does not: secondary_join_field only
    lets two sides use DIFFERENTLY-NAMED fields that hold the SAME kind of
    value (e.g. device.asset_tag == patch_deployment.device_asset_tag) — it
    cannot restrict which ROWS of a shared, multi-purpose table
    (audit_logs, used by every entity type in the system) are eligible to
    join in the first place. audit_logs.entity_id only means "payroll_
    changes.change_id" when entity_type == "payroll_changes"; nothing
    filters the SECONDARY object by entity_type before the join, the same
    unfilled gap as AC-001/MD-005/DP-002 above and below. Confirmed still
    blocked — for a more precise reason than the original "no shared
    join-field name" catalogue entry gave it.

Master Data Management (MD-005):
  - MD-005 "Supplier master changes must be logged": same audit_logs
    polymorphic-join gap as PY-010 above — suppliers has no entity_id
    field, and even substituting supplier_id for it, nothing filters
    audit_logs down to entity_type == "suppliers" rows before the join.

Order-to-Cash (OTC-006, OTC-008):
  - OTC-006: unchanged — sales_orders has no discount-amount field at
    all; discount_approvals is the only place a discount is recorded, so
    there's no way to identify in-scope orders from sales_orders itself.
  - OTC-008: unchanged — receivables.days_overdue is real and usable, but
    no canonical field defines what "overdue" threshold makes a balance a
    monitoring concern.

Change Management (CM-006):
  - CM-006 "Changes outside approved window require review": not
    previously re-stated in the post-0049 gap catalogue's summary list,
    but still blocked for the reason 0047 gave it — the real test compares
    deployed_at (a timestamp) against change_requests.scheduled_window (a
    category like "weekend"/"emergency", not a timestamp or a fixed
    offset). This is neither a relative-date comparison (the reference
    point isn't "now") nor a literal field-to-field comparison of two
    like-typed values — it needs a category-to-time-window translation
    that doesn't exist as a capability.

Revision ID: 0050
Revises: 0049
Create Date: 2026-09-16
"""
import json

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "AC-003",
        "Active account has not logged in for 180+ days (dormant)",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "user",
            "secondary_object": "user",
            "join_field": "user_id",
            "condition_primary": {"field": "status", "operator": "eq", "value": "active"},
            "condition_secondary": {"field": "last_login", "operator": "lte", "value": {"kind": "relative_date", "relative_days": -180}},
        },
    ),
    (
        "AC-006",
        "Access review is stale (not reviewed in 180+ days)",
        {"rule_type": "threshold", "object": "access_reviews", "field": "reviewed_at", "operator": "lte", "value": {"kind": "relative_date", "relative_days": -180}},
    ),
    (
        "SOD-001",
        "Role holds both supplier-creation and payment-approval permissions",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "role_permissions",
            "secondary_object": "role_permissions",
            "join_field": "role",
            "condition_primary": {"field": "permission", "operator": "eq", "value": "create_supplier"},
            "condition_secondary": {"field": "permission", "operator": "eq", "value": "approve_payment"},
        },
    ),
    (
        "SOD-002",
        "Same person created and approved the purchase order",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "purchase_orders",
            "secondary_object": "po_approvals",
            "join_field": "po_number",
            "condition_primary": {"field": "po_number", "operator": "is_not_null"},
            "condition_secondary": {"field": "po_number", "operator": "is_not_null"},
            "field_comparison": {"primary_field": "created_by", "operator": "eq", "secondary_field": "approved_by"},
        },
    ),
    (
        "SOD-004",
        "Same person prepared and approved the journal entry",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "journal_entries",
            "secondary_object": "journal_approvals",
            "join_field": "journal_id",
            "condition_primary": {"field": "journal_id", "operator": "is_not_null"},
            "condition_secondary": {"field": "journal_id", "operator": "is_not_null"},
            "field_comparison": {"primary_field": "prepared_by", "operator": "eq", "secondary_field": "approved_by"},
        },
    ),
    (
        "PR-008",
        "Invoice amount does not match purchase order amount",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "supplier_invoices",
            "secondary_object": "purchase_orders",
            "join_field": "po_number",
            "condition_primary": {"field": "po_number", "operator": "is_not_null"},
            "condition_secondary": {"field": "po_number", "operator": "is_not_null"},
            "field_comparison": {"primary_field": "amount", "operator": "ne", "secondary_field": "amount"},
        },
    ),
    (
        "PR-013",
        "Invoice price does not agree to purchase order price",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "supplier_invoices",
            "secondary_object": "purchase_orders",
            "join_field": "po_number",
            "condition_primary": {"field": "po_number", "operator": "is_not_null"},
            "condition_secondary": {"field": "po_number", "operator": "is_not_null"},
            "field_comparison": {"primary_field": "amount", "operator": "ne", "secondary_field": "amount"},
        },
    ),
    (
        "PR-009",
        "Invoice quantity does not match goods received",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "supplier_invoices",
            "secondary_object": "goods_receipts",
            "join_field": "po_number",
            "condition_primary": {"field": "po_number", "operator": "is_not_null"},
            "condition_secondary": {"field": "po_number", "operator": "is_not_null"},
            "field_comparison": {"primary_field": "quantity", "operator": "ne", "secondary_field": "received_quantity"},
        },
    ),
    (
        "PR-012",
        "Invoice quantity exceeds received quantity",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "supplier_invoices",
            "secondary_object": "goods_receipts",
            "join_field": "po_number",
            "condition_primary": {"field": "po_number", "operator": "is_not_null"},
            "condition_secondary": {"field": "po_number", "operator": "is_not_null"},
            "field_comparison": {"primary_field": "quantity", "operator": "gt", "secondary_field": "received_quantity"},
        },
    ),
    (
        "PR-018",
        "Payment amount does not agree to invoice amount",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "payments",
            "secondary_object": "supplier_invoices",
            "join_field": "invoice_number",
            "condition_primary": {"field": "invoice_number", "operator": "is_not_null"},
            "condition_secondary": {"field": "invoice_number", "operator": "is_not_null"},
            "field_comparison": {"primary_field": "amount", "operator": "ne", "secondary_field": "amount"},
        },
    ),
    (
        "GL-004",
        "Journal entry posted after its accounting period closed",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "journal_entries",
            "secondary_object": "accounting_periods",
            "join_field": "period",
            "condition_primary": {"field": "period", "operator": "is_not_null"},
            "condition_secondary": {"field": "period", "operator": "is_not_null"},
            "field_comparison": {"primary_field": "posted_at", "operator": "gt", "secondary_field": "end_date"},
        },
    ),
    (
        "PY-003",
        "Recently hired employee has no recorded addition approval",
        {
            "rule_type": "missing_match",
            "primary_object": "employee",
            "secondary_object": "employee_approvals",
            "join_field": "employee_id",
            "primary_condition": {"field": "hire_date", "operator": "gte", "value": {"kind": "relative_date", "relative_days": -550}},
        },
    ),
    (
        "OTC-003",
        "Sales order amount exceeds customer credit limit",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "sales_orders",
            "secondary_object": "customers",
            "join_field": "customer_id",
            "condition_primary": {"field": "customer_id", "operator": "is_not_null"},
            "condition_secondary": {"field": "customer_id", "operator": "is_not_null"},
            "field_comparison": {"primary_field": "amount", "operator": "gt", "secondary_field": "credit_limit"},
        },
    ),
    (
        "CM-003",
        "Emergency change has no recorded retrospective approval",
        {
            "rule_type": "missing_match",
            "primary_object": "change_requests",
            "secondary_object": "change_approvals",
            "join_field": "change_id",
            "primary_condition": {"field": "scheduled_window", "operator": "eq", "value": "emergency"},
        },
    ),
    (
        "CM-004",
        "Same person committed and deployed the change",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "code_changes",
            "secondary_object": "deployments",
            "join_field": "change_id",
            "condition_primary": {"field": "change_id", "operator": "is_not_null"},
            "condition_secondary": {"field": "change_id", "operator": "is_not_null"},
            "field_comparison": {"primary_field": "committed_by", "operator": "eq", "secondary_field": "deployed_by"},
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
