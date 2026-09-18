"""Fixes 6 controls where a rule template needed a canonical object its
own control_library.required_tables never listed, or (CM-003) referenced
the wrong table outright.

=== How this was found ===
A full scan comparing every templated control's rule_definition (its
actual required_fields_by_object_for(), resolved through
infer_object_for_entity's aliasing) against its own control_library.
required_tables found 6 real mismatches (after fixing a separate bug in
infer_object_for_entity itself, this same PR, that had been masking two
more: DP-003/DP-004 both self-resolve correctly now that an exact
canonical-object-name match always wins a tie over a same-token-count
superset). required_tables drives the "required tables — bind each one
below" checklist that is a control's ONLY way to bind a table once it
has any required tables at all (DataMappingPanel.hasControlChecklist) —
a canonical object missing from this list can never be bound, so mapping
readiness for it can never be satisfied no matter what the user does.

=== The 6 fixes ===
GL-007 / PR-002 / PR-019: each control's four_way_match template (added
in migration 0071) reads a tertiary "user_roles" object (resolving the
approver's role) that was never added to required_tables when that
migration was written — my own oversight, not a pre-existing bug.

OP-004: cross_match_condition joins job_executions to scheduled_jobs
(comparing actual vs. max allowed duration) — required_tables only ever
listed job_executions, so scheduled_jobs could never be bound.

RA-004: three_way_match's secondary_object is "user" (resolves from a
system_users-shaped table) — required_tables listed hr_employees,
vpn_accounts and remote_access_logs, but no users table at all.

CM-003: NOT a missing-table gap — the template's primary_object was
"change_requests", but required_tables has always said
production_changes + change_approvals, matching the control's own name
("Emergency CHANGES require retrospective approval") and audit_procedure.
This was a copy/paste mistake (change_requests is a different, valid
canonical object used by other Change Management controls) that made
CM-003 permanently unsatisfiable — the checklist would only ever offer
production_changes/change_approvals to bind, but the rule needed a table
mapped to change_requests that nothing on the checklist could ever
produce. Fixed to primary_object: "production_changes" (same join_field
and condition, unchanged).

Revision ID: 0075
Revises: 0074
Create Date: 2026-09-18
"""
import json

from alembic import op
from sqlalchemy import text

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None

_REQUIRED_TABLES_ADDITIONS = {
    "GL-007": "user_roles",
    "PR-002": "user_roles",
    "PR-019": "user_roles",
    "OP-004": "scheduled_jobs",
    "RA-004": "system_users",
}


def upgrade() -> None:
    bind = op.get_bind()
    for control_code, table_to_add in _REQUIRED_TABLES_ADDITIONS.items():
        bind.execute(
            text(
                """
                UPDATE control_library
                SET required_tables = required_tables || to_jsonb(CAST(:table_to_add AS text))
                WHERE control_code = :control_code
                  AND NOT (required_tables @> to_jsonb(CAST(:table_to_add AS text)))
                """
            ),
            {"control_code": control_code, "table_to_add": table_to_add},
        )

    cm003_row = bind.execute(
        text(
            """
            SELECT crt.rule_definition FROM control_rule_templates crt
            JOIN control_library cl ON cl.control_library_id = crt.control_library_id
            WHERE cl.control_code = 'CM-003'
            """
        )
    ).fetchone()
    if cm003_row is not None:
        rule_definition = json.loads(cm003_row[0])
        if rule_definition.get("primary_object") == "change_requests":
            rule_definition["primary_object"] = "production_changes"
            bind.execute(
                text(
                    """
                    UPDATE control_rule_templates
                    SET rule_definition = :rule_definition
                    WHERE control_library_id = (SELECT control_library_id FROM control_library WHERE control_code = 'CM-003')
                    """
                ),
                {"rule_definition": json.dumps(rule_definition)},
            )


def downgrade() -> None:
    bind = op.get_bind()
    for control_code, table_to_remove in _REQUIRED_TABLES_ADDITIONS.items():
        bind.execute(
            text(
                """
                UPDATE control_library
                SET required_tables = (
                    SELECT jsonb_agg(t) FROM jsonb_array_elements(required_tables) AS t
                    WHERE t <> to_jsonb(CAST(:table_to_remove AS text))
                )
                WHERE control_code = :control_code
                """
            ),
            {"control_code": control_code, "table_to_remove": table_to_remove},
        )

    cm003_row = bind.execute(
        text(
            """
            SELECT crt.rule_definition FROM control_rule_templates crt
            JOIN control_library cl ON cl.control_library_id = crt.control_library_id
            WHERE cl.control_code = 'CM-003'
            """
        )
    ).fetchone()
    if cm003_row is not None:
        rule_definition = json.loads(cm003_row[0])
        if rule_definition.get("primary_object") == "production_changes":
            rule_definition["primary_object"] = "change_requests"
            bind.execute(
                text(
                    """
                    UPDATE control_rule_templates
                    SET rule_definition = :rule_definition
                    WHERE control_library_id = (SELECT control_library_id FROM control_library WHERE control_code = 'CM-003')
                    """
                ),
                {"rule_definition": json.dumps(rule_definition)},
            )
