"""Adds a distinct_field option to DuplicateRule and templates AC-008
"Shared accounts should be restricted" with it.

=== The gap ===
AC-008's audit_procedure is "Identify accounts used by multiple people."
The proxy signal available from its own required_tables (system_users,
login_history) is: the same user_id logging in from more than one
distinct ip_address. DuplicateRule already does group_by + count, but
only ever counted ROW repetition of the group_by combination itself —
group_by=["user_id"] alone would flag every account with more than one
login row at all (true of nearly every active account, not a finding).
What AC-008 actually needs is "this group_by key has more than one
DISTINCT value of a DIFFERENT field" — genuinely new behavior, not
expressible with the existing rule primitives (threshold, duplicate,
missing_match, cross_match_condition, three/four_way_match all join or
count rows, none count distinct values of a field per group).

=== What was built ===
DuplicateRule (app/schemas/test_rule.py) gained an optional
distinct_field. When set, the check becomes "group_by has >1 distinct
value of distinct_field" instead of "group_by combination repeats."
Implemented in both engines: app/services/rule_evaluation.py (a per-key
set of non-null distinct_field values) and gateway/gateway/rule_engine.py
(groupby(...)[distinct_field].transform("nunique"), which already
excludes NaN the same way the pure-Python set does by skipping None).
rule_preview_service.py and exception_service.py's plain-English
description/summary builders got a distinct_field-aware branch each, so
this isn't a partially-wired option — an exception raised by it explains
itself exactly like the plain duplicate-rows case does.

Verified with synthetic rows against both engines directly: a user_id
with two login rows from the SAME ip_address does not flag; the same
user_id with a login row from a second, different ip_address flags both
of that user's rows; the two engines produce identical exception_data.

=== The template ===
object=login_history, group_by=["user_id"], distinct_field="ip_address".

Final coverage: 124/157 (was 123/157 after migration 0071).

Revision ID: 0073
Revises: 0072
Create Date: 2026-09-18
"""
import json

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None

_CONTROL_CODE = "AC-008"
_RULE_NAME = "Account logged in from more than one distinct IP address"
_RULE_DEFINITION = {
    "rule_type": "duplicate",
    "object": "login_history",
    "group_by": ["user_id"],
    "distinct_field": "ip_address",
}


def upgrade() -> None:
    rule_definition_json = json.dumps(_RULE_DEFINITION).replace("'", "''")
    op.execute(
        f"""
        INSERT INTO control_rule_templates (control_library_id, rule_name, rule_definition)
        SELECT control_library_id, '{_RULE_NAME}', '{rule_definition_json}'
        FROM control_library WHERE control_code = '{_CONTROL_CODE}'
        ON CONFLICT (control_library_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DELETE FROM control_rule_templates
        WHERE control_library_id IN (SELECT control_library_id FROM control_library WHERE control_code = '{_CONTROL_CODE}')
        """
    )
