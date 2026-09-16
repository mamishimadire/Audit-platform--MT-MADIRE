"""Extend rule-template coverage using ThreeWayMatchRule (rule_type
"three_way_match"), the headline round-3 rule-engine capability — see
app.schemas.test_rule's module docstring for its exact semantics. This is
the first of three migrations completing the third and final rule-template
pass (0052-0054), targeting the 59 controls left untemplated after 0044,
0046-0049, and 0050-0051 (98/157 templated at the start of this pass).

Same validation discipline as every prior rule-template migration: every
rule_definition here was schema-validated against app.schemas.test_rule's
Pydantic models AND dry-run against BOTH engines (app.services.
rule_evaluation, the backend's pandas-free mirror, and gateway.gateway.
rule_engine, the pandas original) with synthetic good/bad rows, confirming
each produces exactly the expected exception set in both.

=== The central finding of this migration: ThreeWayMatchRule is an INNER
    join, with no "missing" (anti-join) equivalent ===
Every prior migration's gap catalogue described most of the remaining
3-way-shaped controls loosely as "needs a 3-way join," which reads as if
ThreeWayMatchRule would mechanically unblock all of them once it existed.
Re-deriving each one individually (against its real audit_procedure, the
seed fixture's actual designated violation, and canonical_model.py) shows
that is only true for controls whose real test is a 3-way RECONCILIATION —
flag rows where an inner join across three tables satisfies some condition
(a genuine three-way match like PO/GRN/invoice, or a chain that resolves to
"bad state present on an already-joined row"). It is NOT true for controls
whose real test is "no matching record exists after chaining through an
intermediate table" — an anti-join, which MissingMatchRule already does at
2 objects but which ThreeWayMatchRule has no equivalent of at 3 objects; it
only ever finds combinations that ARE present, never combinations that are
ABSENT. This distinction is the specific, precise reason several controls
in the "should now be expressible via ThreeWayMatchRule" category (per the
task brief) turn out to still be blocked below — not a vague restatement of
the old gap, but a newly precise one, confirmed by checking each control's
actual seed-fixture violation (where one exists) to see which shape it
actually needs.

A second, independent finding: several of the same controls also need a
FOURTH object in the join chain (e.g. journal_entries -> user -> user_roles
-> approval_limits to resolve a per-role dynamic threshold), which
ThreeWayMatchRule's fixed primary/secondary/tertiary shape cannot express
regardless of the anti-join question. Both reasons are called out
separately per control below, since some controls have one gap, some have
the other, and a few have both.

=== Templated in this migration ===

Procure-to-Pay:
  - PR-011 "Three-way match must be performed": the flagship case
    ThreeWayMatchRule exists for. purchase_orders <-> goods_receipts <->
    supplier_invoices, all three chained on po_number (a genuinely shared
    field name at every hop), field_comparison comparing goods_receipts.
    received_quantity to supplier_invoices.quantity. This is deliberately
    NOT a duplicate of the already-templated PR-009 (0050): PR-009 is a
    two-way invoice-vs-GRN check that never confirms a PO exists at all;
    PR-011 is the genuine three-way reconciliation the control's own name
    describes, anchored through purchase_orders as the primary object.
    Confirmed against the seed fixture: PO1002/GRN2002(qty 50)/
    INV3005(qty 80) is exactly PR-012's named violation, reached here via
    the full three-way chain instead of a two-way shortcut.
  - PR-014 "Payment should not occur before receipt": three_way_match,
    payments <-> supplier_invoices (on invoice_number, shared field name)
    <-> goods_receipts (on po_number, shared field name on both sides),
    field_comparison paid_at < received_at. Deliberately scoped and named
    for exactly this: "payment recorded before its linked goods receipt,
    when both are on file." This is an honest partial fix, matching the
    precedent set by EN-001/RA-008/WF-007 (0051) for "partial-but-honest
    scoping" — it does NOT catch the seed fixture's own PR-014 violation
    (PAY4003, paid against INV3004, which has po_number = None and so has
    no goods_receipts row to chain through at all), because that specific
    case is actually a missing_match / anti-join shape ("paid despite no
    receipt existing"), which — per this migration's central finding —
    ThreeWayMatchRule cannot express. The timing-comparison half this
    template DOES cover is a real, correctly-behaving, non-vacuous test
    (confirmed via this migration's own synthetic dry-run, which
    constructs a receipt recorded after its payment and gets flagged); it
    is just narrower than the control's full wording, and is named
    accordingly rather than implying full coverage.

Remote Access:
  - RA-004 "Terminated employees must have remote access revoked":
    three_way_match, employee <-> user (on employee_id, shared field name)
    <-> vpn_accounts (on user_id, shared field name), condition_primary
    employment_status != "active", condition_tertiary status == "active".
    This is a genuine 3-way INNER-join reconciliation (not an anti-join):
    the violation is a terminated employee's vpn_accounts row still
    showing status "active", which is a row that DOES exist and DOES join
    cleanly across all three tables — exactly the shape ThreeWayMatchRule
    is built for. Confirmed against the seed fixture: EMP007 (terminated)
    -> U007 -> the vpn_accounts row explicitly commented "RA-004
    violation." "user" is used as the bridging secondary object even
    though RA-004's own required_tables list only names hr_employees and
    vpn_accounts — the same table-substitution technique 0047/0050
    established for CM-003 (using change_requests in place of
    production_changes) — because vpn_accounts has no employee_id field of
    its own; user.employee_id/user.user_id is the only real link between
    the two. remote_access_logs (also named in RA-004's required_tables)
    was deliberately NOT used as the tertiary object instead of
    vpn_accounts: it has no revocation-state field (only session/event
    data), so it can only prove someone USED remote access after
    termination, not that access remains GRANTED — vpn_accounts.status is
    the more direct, persistent-state signal this control's wording
    ("must have remote access revoked") actually asks about.

=== Re-examined and confirmed STILL BLOCKED, with the precise reason ===

User Access Management:
  - AC-005 "Privileged access must be authorised": re-derived against the
    seed fixture directly. The users holding a privileged role (U002,
    U007, U013 — it_admin/system_administrator) have NO access_requests
    row at all; only U011 has one, and it's already approved. The real
    violation shape is therefore "privileged role holder with NO
    access_request on file" — a 3-way ANTI-join (user_roles -> roles,
    identify privileged via a role_name regex match, then find role
    holders absent from access_requests) — which this migration's central
    finding says ThreeWayMatchRule cannot express. It is also structurally
    awkward as an inner-join chain regardless: access_requests only shares
    user_id with user_roles, and roles (needed to test "privileged" via
    role_name) has no user_id at all, so a role_name filter can only ever
    sit on the SECONDARY object of a primary(access_requests)<->secondary
    (user_roles)<->tertiary(roles) chain — but then the anti-join problem
    still applies on top. Still blocked, now for a fully precise reason
    instead of a generic "needs a 3-way join."

API Controls:
  - API-002 "API access must be authorised": the seed fixture's own
    comment ("contractor with no formal role/permission on file") names
    an anti-join directly — api_access -> user -> user_roles, flagging
    api_access rows whose user has NO row in user_roles (or, more
    precisely, no role carrying an API-relevant permission — role_
    permissions has no such permission value defined anywhere in the
    canonical data either, a second, independent gap). Still blocked: an
    anti-join at the third hop, the same central finding as AC-005 above.

Financial / General Ledger:
  - GL-002 "Journals must be posted by authorised users": re-confirmed
    still blocked, now for a doubly-precise reason. Resolving journal_
    entries.prepared_by (an employee_id, e.g. "EMP009") to a role requires
    journal_entries -> user (on employee_id) -> user_roles (on user_id) —
    already 3 objects — and the actual authorization test then needs a
    FOURTH object, role_permissions, to check whether that role carries a
    posting-related permission. Even setting the 4-object limit aside,
    role_permissions has no permission value anywhere resembling
    "post_journal" (only create_supplier_invoice/approve_payment/
    create_supplier/create_purchase_order/full_access are defined) — so
    there is no permission concept to bind to even with an unlimited join
    depth. Both reasons are independent and either alone would block this.
  - GL-007 "Journals above threshold require approval": same 4-object
    chain as GL-002's first reason (journal_entries -> user -> user_roles
    -> approval_limits, needed to resolve prepared_by's employee_id to a
    role and then to that role's approval_limits.max_amount) — approval_
    limits IS a real, usable per-role threshold table, unlike GL-002's
    missing permission concept, but the chain to reach it is still one
    object longer than ThreeWayMatchRule supports.
  - GL-010 "GL should reconcile to subledger": unchanged — needs aggregate
    SUM comparisons across general_ledger/ap_transactions/ar_transactions/
    inventory/payroll, a different capability class entirely (see 0054's
    aggregate-gap summary); a join-chain capability doesn't help here
    regardless of its depth.

Patch Management:
  - PM-008 "Patch compliance must be reconciled per device": re-derived —
    the real reconciliation needs asset_register -> system_inventory (for
    the asset's OS) -> patch_catalogue (for which patches apply to that
    OS) -> patch_inventory (for whether that specific patch is actually
    installed on that asset), a 4-object chain, on top of the pre-existing
    "patch_inventory has no unique per-row identifier" problem (0049) that
    would still block any per-asset-per-patch join even at unlimited
    depth. Both reasons independently block this.

Procure-to-Pay:
  - PR-002 "Approval limits must be respected": same 4-object chain
    structure as GL-007 (purchase_orders.created_by -> user -> user_roles
    -> approval_limits).
  - PR-019 "High-value payments require additional approval": same
    4-object chain again (payments.paid_by -> user -> user_roles ->
    approval_limits).

Remote Access:
  - RA-003 "VPN access must be restricted to approved users": no seed-
    fixture violation is tagged for this control, so its real intended
    shape can't be confirmed against a concrete case the way RA-004/AC-005
    above could be — but every plausible reading of "restricted to
    approved users" (a VPN account whose holder has no valid employee
    record, or whose holder has no approved access_request on file) is an
    anti-join once chained through user/employee, the same central-finding
    gap as AC-005/API-002. Guessing at a specific inner-join shape without
    a fixture to confirm it against would risk exactly the "plausible-
    looking but wrong template" this project's one hard constraint
    forbids, so this stays unattempted rather than guessed at.
  - RA-007 "Privileged remote access requires additional approval": the
    seed fixture's own comment ("U011's session used a privileged role
    with no approval on file") names an anti-join directly, even though
    the join fields themselves are now all genuinely available
    (user_roles.user_id == remote_access_logs.user_id, remote_access_logs.
    session_id == privileged_access_approvals.session_id — both literal,
    shared field names, and user_roles.role even matches the "(?i)admin"
    pattern this migration's sibling 0054 migration uses for AC-009,
    catching both "it_admin" and "system_administrator"). The blocker is
    entirely the anti-join question, not a join-field gap: finding
    "privileged sessions with NO privileged_access_approvals row" needs
    the same missing 3-way anti-join capability as AC-005/API-002/RA-003.

Revision ID: 0052
Revises: 0051
Create Date: 2026-09-16
"""
import json

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "PR-011",
        "Purchase order, goods receipt and invoice quantities do not reconcile",
        {
            "rule_type": "three_way_match",
            "primary_object": "purchase_orders",
            "secondary_object": "goods_receipts",
            "tertiary_object": "supplier_invoices",
            "join_field_primary_secondary": "po_number",
            "join_field_secondary_tertiary": "po_number",
            "field_comparison": {
                "left_object": "secondary",
                "left_field": "received_quantity",
                "operator": "ne",
                "right_object": "tertiary",
                "right_field": "quantity",
            },
        },
    ),
    (
        "PR-014",
        "Payment recorded before its linked goods receipt",
        {
            "rule_type": "three_way_match",
            "primary_object": "payments",
            "secondary_object": "supplier_invoices",
            "tertiary_object": "goods_receipts",
            "join_field_primary_secondary": "invoice_number",
            "join_field_secondary_tertiary": "po_number",
            "field_comparison": {
                "left_object": "primary",
                "left_field": "paid_at",
                "operator": "lt",
                "right_object": "tertiary",
                "right_field": "received_at",
            },
        },
    ),
    (
        "RA-004",
        "Terminated employee still has an active VPN account",
        {
            "rule_type": "three_way_match",
            "primary_object": "employee",
            "secondary_object": "user",
            "tertiary_object": "vpn_accounts",
            "join_field_primary_secondary": "employee_id",
            "join_field_secondary_tertiary": "user_id",
            "condition_primary": {"field": "employment_status", "operator": "ne", "value": "active"},
            "condition_tertiary": {"field": "status", "operator": "eq", "value": "active"},
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
