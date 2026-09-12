"""Seed real, verified rule templates for the 5 MVP domains (User Access
Management, Change Management, Procure-to-Pay, Financial/GL, IT Operations/
Backup & Recovery) — the domains already prioritized as MVP scope for this
project, per the pre-built control library's own priority order.

Same discipline as migration 0027 (AC-002, the only template that existed
before this one): every rule_definition here was schema-validated against
app.schemas.test_rule's Pydantic models AND dry-run against the real
Gateway rule engine (gateway/gateway/rule_engine.py) with synthetic
good/bad rows to confirm it produces the expected exception, not just
"it parses." A control from these domains that isn't seeded here was
deliberately left out because the current 4 rule primitives (threshold,
duplicate, missing_match, cross_match_condition) cannot express its real
procedure correctly — see the skip notes below. Guessing at a plausible-
looking but wrong template would be worse than no template at all.

Two patterns used repeatedly here, worth naming since they're not obvious
from the schema alone:
  - "Pass-through condition": cross_match_condition requires a condition
    on BOTH sides, but sometimes only one side actually needs filtering
    (e.g. "PO issued to an inactive supplier" only filters suppliers, not
    POs). A condition that's true for every real row (is_not_null on a
    column that's always populated) makes that side act as an unfiltered
    inner join, without inventing a rule type that doesn't exist yet.
  - "Self-join": the same object as both primary_object and
    secondary_object lets cross_match_condition express an AND of two
    conditions on ONE table (e.g. OP-006: severity=critical AND
    status!=resolved on `incidents`) — each side is independently filtered,
    then merged back together on the row's own id, which only matches
    itself.

Controls deliberately NOT seeded from these 5 domains, and the specific
engine gap each needs (not a vague "too hard" — a concrete missing
capability, so the next person doesn't have to re-derive this):
  - AC-001, AC-003, AC-005, AC-006, AC-007, AC-008, AC-009: need a
    *filtered* missing-match (flag only PRIMARY rows matching a condition
    that also lack a secondary match — e.g. "active users with no
    approved request"), OR a relative-date operator (dormancy/staleness
    windows measured from "now"), OR string pattern matching (generic
    account name detection) — none of which the current 4 primitives
    support.
  - CM-003, CM-004, CM-006: need either a relative-date window (retrospective
    approval period, deployment window) or a field-to-field comparison
    across the two joined tables (developer_id == deployer_id) — FieldCondition
    only compares a field to a fixed value, never to another table's column.
  - GL-002, GL-003, GL-004, GL-006, GL-007, GL-008, GL-009, GL-010: need
    permission-set lookups, relative-date period boundaries, aggregate
    sum-vs-sum comparison, or 3+-table reconciliation — no current
    primitive aggregates or compares two computed sums.
  - PR-002, PR-004, PR-008, PR-009, PR-011, PR-012, PR-013, PR-014,
    PR-018, PR-019, PR-020: need field-to-field comparison after a join
    (invoice amount vs PO amount, invoiced qty vs received qty), a 3-way
    join (PR-011's PO+GRN+invoice match), or a dynamic per-role/per-tier
    threshold looked up from another table — none expressible today.
  - BK-001 through BK-006 (the entire domain): every one needs either a
    filtered missing-match (critical systems only) or a relative-date
    window (retention, recovery-test recency) — zero controls in this
    domain are expressible without at least one engine extension.
  - OP-002, OP-003, OP-004, OP-005, OP-007: same filtered-missing-match or
    relative-date gaps (OP-004's per-job threshold also needs a dynamic
    lookup, not a fixed value).

Revision ID: 0044
Revises: 0043
Create Date: 2026-09-12
"""
import json

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

# (control_code, rule_name, rule_definition)
_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "AC-004",
        "User account has no matching HR employee record",
        {"rule_type": "missing_match", "primary_object": "user", "secondary_object": "employee", "join_field": "employee_id"},
    ),
    (
        "AC-010",
        "Inactive employee still has an active account",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "employee",
            "secondary_object": "user",
            "join_field": "employee_id",
            "condition_primary": {"field": "employment_status", "operator": "ne", "value": "active"},
            "condition_secondary": {"field": "status", "operator": "eq", "value": "active"},
        },
    ),
    (
        "CM-001",
        "Production change has no recorded approval",
        {"rule_type": "missing_match", "primary_object": "production_changes", "secondary_object": "change_approvals", "join_field": "change_id"},
    ),
    (
        "CM-002",
        "Change request has no test evidence on file",
        {"rule_type": "missing_match", "primary_object": "change_requests", "secondary_object": "test_results", "join_field": "change_id"},
    ),
    (
        "CM-005",
        "Production change has no corresponding change request",
        {"rule_type": "missing_match", "primary_object": "production_changes", "secondary_object": "change_requests", "join_field": "change_id"},
    ),
    (
        "CM-007",
        "Production log event has no matching approved change",
        {"rule_type": "missing_match", "primary_object": "production_logs", "secondary_object": "change_requests", "join_field": "change_id"},
    ),
    (
        "GL-001",
        "Journal entry has no recorded approval",
        {"rule_type": "missing_match", "primary_object": "journal_entries", "secondary_object": "journal_approvals", "join_field": "journal_id"},
    ),
    (
        "GL-005",
        "Duplicate journal entry (same account, amount and posting date)",
        {"rule_type": "duplicate", "object": "journal_entries", "group_by": ["account", "amount", "posted_at"]},
    ),
    (
        "PR-001",
        "Purchase order has no recorded approval",
        {"rule_type": "missing_match", "primary_object": "purchase_orders", "secondary_object": "po_approvals", "join_field": "po_number"},
    ),
    (
        "PR-003",
        "Purchase order issued to an inactive supplier",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "purchase_orders",
            "secondary_object": "suppliers",
            "join_field": "supplier_id",
            "condition_primary": {"field": "po_number", "operator": "is_not_null"},
            "condition_secondary": {"field": "status", "operator": "eq", "value": "inactive"},
        },
    ),
    (
        "PR-005",
        "Duplicate purchase order number",
        {"rule_type": "duplicate", "object": "purchase_orders", "group_by": ["po_number"]},
    ),
    (
        "PR-006",
        "Duplicate supplier invoice number",
        {"rule_type": "duplicate", "object": "supplier_invoices", "group_by": ["supplier_id", "invoice_number"]},
    ),
    (
        "PR-007",
        "Invoice references an inactive supplier",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "supplier_invoices",
            "secondary_object": "suppliers",
            "join_field": "supplier_id",
            "condition_primary": {"field": "invoice_number", "operator": "is_not_null"},
            "condition_secondary": {"field": "status", "operator": "eq", "value": "inactive"},
        },
    ),
    (
        "PR-010",
        "Supplier invoice has no recorded approval",
        {"rule_type": "missing_match", "primary_object": "supplier_invoices", "secondary_object": "invoice_approvals", "join_field": "invoice_number"},
    ),
    (
        "PR-015",
        "Payment has no recorded approval",
        {"rule_type": "missing_match", "primary_object": "payments", "secondary_object": "payment_approvals", "join_field": "payment_id"},
    ),
    (
        "PR-016",
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
        "PR-017",
        "Duplicate payment (same supplier, invoice and amount)",
        {"rule_type": "duplicate", "object": "payments", "group_by": ["supplier_id", "invoice_number", "amount"]},
    ),
    (
        "OP-001",
        "Critical job has a failed execution",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "scheduled_jobs",
            "secondary_object": "job_executions",
            "join_field": "job_id",
            "condition_primary": {"field": "critical", "operator": "eq", "value": True},
            "condition_secondary": {"field": "status", "operator": "eq", "value": "failed"},
        },
    ),
    (
        "OP-006",
        "Critical incident is still unresolved",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "incidents",
            "secondary_object": "incidents",
            "join_field": "incident_id",
            "condition_primary": {"field": "severity", "operator": "eq", "value": "critical"},
            "condition_secondary": {"field": "status", "operator": "ne", "value": "resolved"},
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
