"""Adds FourWayMatchRule — a new rule-engine primitive, one hop past
ThreeWayMatchRule — and templates the 3 controls this unblocks: PR-002,
PR-019, GL-007.

=== The gap this closes ===
0052's own analysis (re-confirmed in 0059/0060) identified PR-002
"Approval limits must be respected", PR-019 "High-value payments require
additional approval", and GL-007 "Journals above threshold require
approval" as needing a dynamic per-row threshold looked up from
approval_limits, keyed by ROLE — but the approver's role is never on the
transaction record itself, only on a SEPARATE approval record one hop
away (purchase_orders -> po_approvals.approved_by, payments ->
payment_approvals.approved_by, journal_entries -> journal_approvals.
approved_by). Resolving that chain end to end is genuinely 4 objects:
  transaction -> its own approval record -> the approver's role
  (user_roles) -> that role's authorised limit (approval_limits)
ThreeWayMatchRule's own built-in threshold-lookup shape (see its
docstring) only reaches a limits table ONE hop from primary — exactly
what covers a control whose own actor field IS the join key into the
limits table directly. It cannot reach a limits table that's two hops
away. Note this is NOT the "does an approval exist at all" question —
that's already covered by each domain's own sibling control (PR-001
"POs require approval", PR-015 "Payments require approval", GL-001
"Journal entries require approval", all via missing_match). PR-002/
PR-019/GL-007 specifically test whether an approval THAT EXISTS came
from someone with sufficient authority for the amount — an inner-join
question, not an anti-join one.

=== What was built ===
FourWayMatchRule (app/schemas/test_rule.py): primary <-> secondary <->
tertiary <-> quaternary, chained the same way ThreeWayMatchRule chains
three (each join's "other side" field name defaults to the same name,
override when it differs), with the same optional per-side FieldCondition
filters, FourWayFieldComparison (any two of the four sides), and
FourWayDynamicRelativeDateComparison (any two of the four sides) —
structurally identical to ThreeWayMatchRule's own three-object versions,
one role wider. Implemented identically in both engines exactly the same
way ThreeWayMatchRule was: app/services/rule_evaluation.py (pure Python,
nested-dict lookups one level deeper) and gateway/gateway/rule_engine.py
(pandas, one more add_suffix + merge). Every other place that branches on
rule_type for three_way_match — exception_service.py's plain-English
summary and remediation text, exception_trace_service.py's trace-object
role list, rule_preview_service.py's SQL-like preview — got the matching
four_way_match branch, one role wider, so this isn't a partially-wired
primitive: an exception raised by it explains and traces exactly like
every other rule shape.

Verified with synthetic good/bad rows against BOTH engines directly
(not just schema-validated) before templating below — a clerk-approved
payment over the clerk's own limit correctly flags, a manager-approved
payment of the same amount correctly does not, and the two engines
produce byte-identical exception_data for the same input.

=== The 3 templates ===
All three use the identical shape: primary = the transaction (amount),
secondary = its own approvals table (approved_by), tertiary =
user_roles (resolves approved_by's role), quaternary = approval_limits
(the role's max_amount). field_comparison flags primary.amount > that
role's quaternary.max_amount.

Final coverage: 123/157 (was 120/157 after migration 0060; 0061 was a
bugfix with no new coverage).

Revision ID: 0071
Revises: 0070
Create Date: 2026-09-18
"""
import json

from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "PR-002",
        "Purchase order amount exceeds its approver's role limit",
        {
            "rule_type": "four_way_match",
            "primary_object": "purchase_orders",
            "secondary_object": "po_approvals",
            "tertiary_object": "user_roles",
            "quaternary_object": "approval_limits",
            "join_field_primary_secondary": "po_number",
            "join_field_secondary_tertiary": "approved_by",
            "tertiary_join_field": "user_id",
            "join_field_tertiary_quaternary": "role",
            "field_comparison": {
                "left_object": "primary", "left_field": "amount",
                "right_object": "quaternary", "right_field": "max_amount",
                "operator": "gt",
            },
        },
    ),
    (
        "PR-019",
        "Payment amount exceeds its approver's role limit",
        {
            "rule_type": "four_way_match",
            "primary_object": "payments",
            "secondary_object": "payment_approvals",
            "tertiary_object": "user_roles",
            "quaternary_object": "approval_limits",
            "join_field_primary_secondary": "payment_id",
            "join_field_secondary_tertiary": "approved_by",
            "tertiary_join_field": "user_id",
            "join_field_tertiary_quaternary": "role",
            "field_comparison": {
                "left_object": "primary", "left_field": "amount",
                "right_object": "quaternary", "right_field": "max_amount",
                "operator": "gt",
            },
        },
    ),
    (
        "GL-007",
        "Journal amount exceeds its approver's role limit",
        {
            "rule_type": "four_way_match",
            "primary_object": "journal_entries",
            "secondary_object": "journal_approvals",
            "tertiary_object": "user_roles",
            "quaternary_object": "approval_limits",
            "join_field_primary_secondary": "journal_id",
            "join_field_secondary_tertiary": "approved_by",
            "tertiary_join_field": "user_id",
            "join_field_tertiary_quaternary": "role",
            "field_comparison": {
                "left_object": "primary", "left_field": "amount",
                "right_object": "quaternary", "right_field": "max_amount",
                "operator": "gt",
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
