"""Fixes AC-001's rule template, which was written (migration 0059)
referencing a canonical object called "system_users" — that object does
not exist; the platform's actual canonical name for this data, used
consistently everywhere else (AC-002's flagship example included), is
"user" (see app/core/canonical_model.py: CANONICAL_MODEL["user"] =
["user_id", "username", "status", "last_login", "employee_id", "roles"]).

Consequence while this was live: AC-001's mapping-readiness check reported
"still missing: system_users.status, system_users.user_id" even for an
organization that had already approved mappings for exactly this data
under the correct canonical name "user" — the same physical columns, just
checked against a canonical object name that was never real, so nothing
could ever satisfy it. "Generate from control template" could never
succeed for AC-001 until this was fixed, no matter how complete the
org's actual mapping was.

Verified this was the ONLY such mistake across all 120 templated
controls at the time of this migration: every control_rule_templates
row's required_objects_for()/required_fields_by_object_for() output was
checked against CANONICAL_MODEL directly (object names AND field names),
and AC-001 was the sole mismatch found — see the validation script run
during this pass (not checked in, per the existing project convention of
ephemeral validation scripts).

Revision ID: 0061
Revises: 0060
Create Date: 2026-09-17
"""
import json

from alembic import op
from sqlalchemy import text

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None

_OLD_DEFINITION = {
    "rule_type": "missing_match",
    "primary_object": "system_users",
    "secondary_object": "access_requests",
    "join_field": "user_id",
    "primary_condition": {"field": "status", "operator": "eq", "value": "active"},
    "secondary_condition": {"field": "status", "operator": "eq", "value": "approved"},
}

_NEW_DEFINITION = {
    "rule_type": "missing_match",
    "primary_object": "user",
    "secondary_object": "access_requests",
    "join_field": "user_id",
    "primary_condition": {"field": "status", "operator": "eq", "value": "active"},
    "secondary_condition": {"field": "status", "operator": "eq", "value": "approved"},
}


def upgrade() -> None:
    new_json = json.dumps(_NEW_DEFINITION)
    op.execute(
        text(
            """
            UPDATE control_rule_templates
            SET rule_definition = :new_json
            WHERE control_library_id IN (SELECT control_library_id FROM control_library WHERE control_code = 'AC-001')
            """
        ).bindparams(new_json=new_json)
    )


def downgrade() -> None:
    old_json = json.dumps(_OLD_DEFINITION)
    op.execute(
        text(
            """
            UPDATE control_rule_templates
            SET rule_definition = :old_json
            WHERE control_library_id IN (SELECT control_library_id FROM control_library WHERE control_code = 'AC-001')
            """
        ).bindparams(old_json=old_json)
    )
