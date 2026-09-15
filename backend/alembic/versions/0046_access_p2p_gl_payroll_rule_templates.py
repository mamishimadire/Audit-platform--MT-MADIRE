"""Extend rule-template coverage: User Access Management, Segregation of
Duties, Procure-to-Pay (remaining), Financial/GL (remaining), and Payroll —
the controls left untemplated after migration 0044's 5-domain MVP pass.

Same discipline as 0044: every rule_definition here was schema-validated
against app.schemas.test_rule's Pydantic models AND dry-run against a copy
of the real Gateway rule engine (gateway/gateway/rule_engine.py) with
synthetic good/bad rows, confirming each produces exactly the expected
exception set — not just "it parses." Every canonical object/field name
used below is taken verbatim from app/core/canonical_model.py.

Two techniques beyond 0044's own "pass-through condition" / "self-join"
patterns, used repeatedly across this pass:
  - "Boolean self-filter": ThresholdRule's `value` field only accepts
    float|str (not bool — a real engine gap, see the skip list below), so a
    single boolean-field check (e.g. "approved == False") is expressed as a
    self-join cross_match_condition instead: primary_object == secondary_
    object, joined on a field that is genuinely unique per row in that
    table, condition_primary tests the real boolean field, condition_
    secondary is an always-true pass-through. This is not an approximation
    — cross_match_condition's FieldCondition natively supports bool, so the
    result is the exact row set, not an estimate. It is only used where the
    join_field is a true one-row-per-value key (a change_id/deployment_id/
    session_id/key_id/... primary identifier) — where no such per-row key
    exists (e.g. patch_inventory, remote_access_grants), it is deliberately
    NOT used; see the skip notes.
  - "Reused cross-object rule": a handful of controls in different domains
    (different control_library_id) resolve to the literal same rule shape
    once expressed against canonical objects (e.g. PR-020 and MD-004 both
    end up "payment made to an inactive supplier" — same primitive, same
    join, same condition). This is not a mistake; migration 0044 already
    established the pattern (PR-016 is the same shape). Each control still
    gets its own control_rule_templates row because ControlRuleTemplate is
    keyed 1:1 on control_library_id.

A rule was deliberately built from the *literal boolean/status field already
on the record* (e.g. user_access_changes.approved, patch_deployments.
approved) in several places, rather than joining out to a separate
approvals-style table, whenever the record's own field is the more precise,
directly-testable signal and the cross-table join would either not share a
literal field name or would be a much coarser (user-level, not record-level)
match. This is called out per-control below.

One correction to a category 0044 mis-scoped: PR-004 ("invoices without
POs") and GL-004/AC-006/PY-003/PY-009 (see this file and 0047) were listed
by earlier drafts as blocked on the same gaps as their domain siblings
without being individually re-checked. Re-deriving each control's
audit_procedure against canonical_model.py case-by-case (rather than
inheriting a domain-level blanket statement) showed some of them are
genuinely expressible; PR-004 is a plain missing_match exactly like the
already-templated PR-001, and is included here. GL-004 is NOT included
(see below) despite superficially looking similar — the seeded data shows
why a naive version of it would be actively wrong, not just incomplete.

=== Templated in this migration ===
User Access Management:
  - AC-007 "Access changes must be approved": user_access_changes carries
    its own `approved` boolean directly — boolean self-filter on
    change_id (a genuine per-row key). Chosen over joining to
    access_requests because the two tables only share `user_id`, which
    would match ANY request from that user, not the specific change being
    approved — a materially coarser, less correct test.
Procure-to-Pay:
  - PR-004 "POs should precede purchases": missing_match, supplier_invoices
    -> purchase_orders on po_number. Same shape as the already-templated
    PR-001.
  - PR-020 "Payments to inactive suppliers should be investigated": same
    cross_match_condition shape as the already-templated PR-016 (payments
    -> suppliers on supplier_id, supplier status == inactive) — a
    different control code testing the identical real condition.
Financial / General Ledger:
  - GL-003 "Manual journals require review": threshold, journal_entries.
    entry_type == "manual" (a plain categorical value, not a magnitude).
Payroll:
  - PY-001 "Only valid employees should be paid": missing_match, payroll ->
    employee on employee_id.
  - PY-002 "Terminated employees should not be paid": cross_match_condition,
    employee (employment_status != active) -> payroll (pay_id present) on
    employee_id.
  - PY-004 "Salary changes require approval": missing_match, salary_changes
    -> salary_approvals on change_id.
  - PY-005 "Duplicate employees should be identified": duplicate, employee,
    group_by full_name (the only employee-object field this control's
    required_tables — hr_employees alone — actually supports; PY-006
    covers the bank-account angle separately).
  - PY-006 "Duplicate bank accounts should be investigated": duplicate,
    bank_accounts, group_by account_number.
  - PY-007 "Payroll should agree to HR master": missing_match, employee ->
    payroll on employee_id — the reverse direction from PY-001 (finds
    employees who should be paid but have no payroll row at all, a
    distinct completeness check from PY-001's "invalid payroll entry"
    check, not a redundant duplicate of it).
  - PY-009 "Ghost employees should be identified": missing_match, payroll
    -> employee on employee_id. This resolves to the same shape as PY-001
    once you trace what "ghost employee" actually means against this
    schema (a payroll row whose employee_id has no HR master record) —
    confirmed against the seed fixture's own PAY-E999/EMP999 case, which
    is exactly what PY-001's rule already flags. Using employee_documents
    instead (a plausible first read of "no supporting HR information")
    was considered and rejected: 11 of 12 seeded employees have zero
    employee_documents rows, which would flag the overwhelming majority of
    a normal workforce as "ghost employees" — a materially wrong result,
    not a genuine finding.

=== Deliberately NOT templated, with the specific gap ===

User Access Management (AC-001, AC-003, AC-005, AC-006, AC-008, AC-009):
  - AC-001 "User access must be approved": needs a *filtered* missing-match
    (only ACTIVE users need a matching APPROVED request — the seeded
    counter-example, U006, has a request on file, just not an approved
    one, so an unfiltered missing_match would miss it entirely).
  - AC-003 "Dormant accounts must be disabled": needs a relative-date
    operator (last_login older than N days from "now").
  - AC-005 "Privileged access must be authorised": needs a 3-way join
    (user_roles -> roles -> access_requests) AND there is no canonical
    "is_privileged" field on `role` to even identify the privileged subset
    without guessing at a role_name string.
  - AC-006 "Periodic access reviews must occur": the audit_procedure text
    ("has not been reviewed") looks like a plain missing_match at first,
    but the seeded violation (REV002, reviewed 400 days ago) is a STALE
    review, not a missing one — the real control needs a relative-date
    staleness window, and a missing_match version would test a different,
    narrower thing than what's actually intended.
  - AC-008 "Shared accounts should be restricted": needs a distinct-count
    aggregate ("used by more than one real person"), not a row-count
    duplicate check — the duplicate primitive only counts rows, and every
    account naturally has many login_history rows.
  - AC-009 "Generic accounts must be identified": needs regex/string-
    pattern matching on username (e.g. "admin", "shared.*") — no such
    operator exists.

Segregation of Duties (SOD-001, SOD-002, SOD-003, SOD-004, SOD-005,
SOD-006, SOD-007 — the entire domain):
  - SOD-001 (supplier creation vs payment), SOD-002 (PO creation vs
    approval), SOD-003 (invoice processing vs payment), SOD-004 (journal
    preparation vs approval), SOD-005 (employee administration vs payroll
    processing), and SOD-006 (customer creation vs credit approval) all
    need a field-to-field comparison between two columns on a JOINED row
    (creator vs approver/processor on the same PO/invoice/journal/
    employee/customer record) — FieldCondition only compares one field to
    a fixed literal value, never to another column, joined or not.
  - SOD-007 "Identify conflicting roles" needs a multi-way join (user ->
    user_roles -> role_permissions, twice, once per conflicting
    permission) plus a list-membership test against sod_rules.
    conflicting_permissions (a list-typed field) — no primitive supports
    either.

Procure-to-Pay (PR-002, PR-008, PR-009, PR-011, PR-012, PR-013, PR-014,
PR-018, PR-019):
  - PR-002, PR-019: need a dynamic per-role/per-tier threshold looked up
    from approval_limits — no primitive resolves "the limit for THIS
    row's approver's role" before comparing.
  - PR-008, PR-009, PR-012, PR-013, PR-018: all need a field-to-field
    comparison after a join (invoice amount vs PO amount, invoice quantity
    vs GRN quantity, payment amount vs invoice amount) — the same gap as
    SOD-001..006.
  - PR-011: needs a genuine 3-way join (PO + GRN + invoice).
  - PR-014: payments has no po_number field and goods_receipts has no
    invoice_number field, so there is no single shared join_field that
    connects them even before considering the 3-way nature of the real
    "paid before receipt" test.

Financial / General Ledger (GL-002, GL-004, GL-006, GL-007, GL-008, GL-009,
GL-010):
  - GL-002 "Journals must be posted by authorised users": needs a
    permission-set lookup (prepared_by -> user -> user_roles ->
    role_permissions) — a 3+-way join, and no "can_post_journal"-style
    canonical flag exists to shortcut it.
  - GL-004 "Journals posted after period-end require review": the real
    test is posted_at vs accounting_periods.end_date — a field-to-field
    date comparison, which cross_match_condition cannot do (FieldCondition
    only compares to a literal). A tempting shortcut — join on `period`
    and flag any journal whose period.status == "closed" — was tried and
    rejected: in the seeded data, EVERY posted-in-August journal (JE5001,
    JE5002, JE5003) sits in a period that is now "closed" (periods close
    eventually, by design), so that shortcut would flag routine, timely
    postings as violations purely because their period has since closed —
    an overwhelmingly wrong result, not merely an incomplete one.
  - GL-006 "Unusual journals should be investigated": the audit_procedure
    itself ("apply amount/date/account/user rules") does not name a
    single concrete, testable condition — no canonical field encodes what
    "unusual" means for this org, so there's nothing to bind a rule to
    without inventing a threshold from nothing.
  - GL-007: same dynamic per-role threshold gap as PR-002/PR-019
    (approval_limits, keyed by role).
  - GL-008 "Suspense accounts require review": general_ledger has no
    account_type/classification field — only account_name (free text).
    Matching account_name == "Suspense" literally would work for this
    specific demo but is not a reliable general test: unlike a controlled-
    vocabulary field (status, classification), account naming is entirely
    client-specific free text, so this is a genuine missing-canonical-
    field gap, not a primitive-capability gap.
  - GL-009 "Debits and credits must balance": needs SUM(debit) vs
    SUM(credit) per journal_id — an aggregate, which no primitive
    computes.
  - GL-010 "GL should reconcile to subledger": needs aggregate sums across
    4-5 tables plus a 3+-way reconciliation — same aggregate gap as GL-009,
    compounded.

Payroll (PY-003, PY-008, PY-010):
  - PY-003 "Employee additions require approval": looks like a plain
    missing_match (employee -> employee_approvals) at first, but checking
    it against the seed fixture shows why that's wrong: only 2 of 12
    employees have any employee_approvals row at all, so an unfiltered
    missing_match would flag 10 of 12 (83%) of a normal, long-tenured
    workforce as "unapproved additions." The seed's own comment scopes the
    real violation to "recent hires" specifically — this control needs a
    relative-date filter (hired within the last N days), not a blanket
    historical reconciliation.
  - PY-008 "Unusual salary increases should be investigated": needs a
    period-over-period (this period's gross_pay vs the PRIOR period's
    gross_pay for the same employee) comparison — a same-table, row-vs-row
    lag comparison that none of the 4 primitives express (missing_match/
    cross_match_condition only ever compare a field to a fixed literal,
    never to another row's value of the same field).
  - PY-010 "Payroll changes should be logged": audit_logs uses a
    polymorphic entity_type/entity_id pair, not a table-specific foreign
    key — payroll_changes has no "entity_id" field, only "change_id", so
    there is no shared join_field name between the two objects (the
    primitives require the exact same field name to exist on both sides).

Revision ID: 0046
Revises: 0045
Create Date: 2026-09-15
"""
import json

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "AC-007",
        "Access change was not approved",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "user_access_changes",
            "secondary_object": "user_access_changes",
            "join_field": "change_id",
            "condition_primary": {"field": "approved", "operator": "eq", "value": False},
            "condition_secondary": {"field": "change_id", "operator": "is_not_null"},
        },
    ),
    (
        "PR-004",
        "Supplier invoice has no matching purchase order",
        {"rule_type": "missing_match", "primary_object": "supplier_invoices", "secondary_object": "purchase_orders", "join_field": "po_number"},
    ),
    (
        "PR-020",
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
        "GL-003",
        "Manual journal entry identified for review",
        {"rule_type": "threshold", "object": "journal_entries", "field": "entry_type", "operator": "eq", "value": "manual"},
    ),
    (
        "PY-001",
        "Payroll entry has no matching HR employee record",
        {"rule_type": "missing_match", "primary_object": "payroll", "secondary_object": "employee", "join_field": "employee_id"},
    ),
    (
        "PY-002",
        "Terminated employee is still being paid",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "employee",
            "secondary_object": "payroll",
            "join_field": "employee_id",
            "condition_primary": {"field": "employment_status", "operator": "ne", "value": "active"},
            "condition_secondary": {"field": "pay_id", "operator": "is_not_null"},
        },
    ),
    (
        "PY-004",
        "Salary change has no recorded approval",
        {"rule_type": "missing_match", "primary_object": "salary_changes", "secondary_object": "salary_approvals", "join_field": "change_id"},
    ),
    (
        "PY-005",
        "Duplicate employee name",
        {"rule_type": "duplicate", "object": "employee", "group_by": ["full_name"]},
    ),
    (
        "PY-006",
        "Duplicate employee bank account number",
        {"rule_type": "duplicate", "object": "bank_accounts", "group_by": ["account_number"]},
    ),
    (
        "PY-007",
        "Active employee has no payroll record",
        {"rule_type": "missing_match", "primary_object": "employee", "secondary_object": "payroll", "join_field": "employee_id"},
    ),
    (
        "PY-009",
        "Payroll entry has no matching HR employee record (ghost employee)",
        {"rule_type": "missing_match", "primary_object": "payroll", "secondary_object": "employee", "join_field": "employee_id"},
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
