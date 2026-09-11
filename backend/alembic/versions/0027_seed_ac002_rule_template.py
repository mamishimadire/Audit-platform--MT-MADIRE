"""Seed one real, human-verified rule template: AC-002

Deliberately just one. AC-002 ("terminated employees must have access
removed") is the platform's own flagship worked example — its cross-match
logic (employee.employment_status=terminated AND user.status=active,
joined on employee_id) has been built, tested, and verified end-to-end
this session. Every other control in the ~157-control library is left
without a template rather than guessing at rule logic we have no real
specification for — see control_rule_templates' model docstring.

AS-004 (software compliance) is NOT seeded here despite being active and
working: it's evaluated by bespoke Python logic
(software_compliance_service.py), not the generic TestRule/rule_definition
engine this template mechanism generates into — it doesn't fit the same
shape without a larger refactor.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-07

"""
import json

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

_RULE_DEFINITION = {
    "rule_type": "cross_match_condition",
    "primary_object": "employee",
    "secondary_object": "user",
    "join_field": "employee_id",
    "condition_primary": {"field": "employment_status", "operator": "eq", "value": "terminated"},
    "condition_secondary": {"field": "status", "operator": "eq", "value": "active"},
}


def upgrade() -> None:
    rule_definition_json = json.dumps(_RULE_DEFINITION).replace("'", "''")
    op.execute(
        f"""
        INSERT INTO control_rule_templates (control_library_id, rule_name, rule_definition)
        SELECT control_library_id, 'Terminated employee still has active access', '{rule_definition_json}'
        FROM control_library
        WHERE control_code = 'AC-002'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM control_rule_templates
        WHERE control_library_id = (SELECT control_library_id FROM control_library WHERE control_code = 'AC-002')
        """
    )
