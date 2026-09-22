"""Fixes CM-003 for real this time: `scheduled_window` is not, and was never,
a field of `production_changes` — 0075's own fix pointed the rule at the
wrong table.

=== How this was found ===
A full scan comparing every templated control's rule_definition (its actual
required_fields_by_object_for()) against CANONICAL_MODEL — every field a
template asks for must be a real field of the canonical object it's asked
against, the same discipline 0075 already applied to required_tables vs.
rule objects. One mismatch across all 157 templates: CM-003 requires
`production_changes.scheduled_window`, but `production_changes`'s only
fields are change_id/deployed_at/deployed_by (canonical_model.py) —
`scheduled_window` (the pre-deployment "when is this planned for, and is it
an emergency" flag) is a `change_requests` field. No org can ever satisfy
this: a real production_changes table has no such concept to map, so
"Generate from control template" is permanently blocked, exactly as seen
live on Bidvest ALICE's CM-003.

=== What actually happened ===
Migration 0050 seeded CM-003 correctly: primary_object "change_requests".
0075 found that `change_requests` wasn't in CM-003's own required_tables
(so the binding checklist could never offer it) and "fixed" this by
repointing the RULE at `production_changes` instead, reasoning the control's
own name ("changes require retrospective approval") and required_tables
already said production_changes. That reasoning held for the object, but
not the field: production_changes is the deployed-change record and never
carried a scheduling/emergency flag — only change_requests (the pre-
deployment record) does. 0075 traded one unsatisfiable requirement
(an unbindable object) for a different one (an unmappable field).

=== The real fix ===
Revert the rule to `change_requests` (0050's original), and this time also
fix the actual gap 0075 was reacting to: add `change_requests` to CM-003's
required_tables and drop `production_changes` from it — the rule never
reads production_changes at all, so requiring it bound was always dead
weight (found the same day as this fix, from a live "you're asking me to
map a field that doesn't exist" report). Any org that already bound
production_changes for CM-003 (Bidvest ALICE does) keeps that binding row —
it simply stops being required and stops showing on the checklist; nothing
deletes it. Guarded the same way as 0075/0082: only touches rows still at
the exact known-broken state, so a later deliberate edit is never clobbered.

Revision ID: 0084
Revises: 0083
Create Date: 2026-09-22
"""
import json

from alembic import op
from sqlalchemy import text

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None

_BROKEN_DEFINITION = {
    "rule_type": "missing_match",
    "primary_object": "production_changes",
    "secondary_object": "change_approvals",
    "join_field": "change_id",
    "primary_condition": {"field": "scheduled_window", "operator": "eq", "value": "emergency"},
}
_FIXED_DEFINITION = {
    "rule_type": "missing_match",
    "primary_object": "change_requests",
    "secondary_object": "change_approvals",
    "join_field": "change_id",
    "primary_condition": {"field": "scheduled_window", "operator": "eq", "value": "emergency"},
}


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            """
            UPDATE control_rule_templates
               SET rule_definition = :fixed
             WHERE control_library_id IN (SELECT control_library_id FROM control_library WHERE control_code = 'CM-003')
               AND CAST(rule_definition AS jsonb) = CAST(:broken AS jsonb)
            """
        ),
        {"fixed": json.dumps(_FIXED_DEFINITION), "broken": json.dumps(_BROKEN_DEFINITION)},
    )
    conn.execute(
        text(
            """
            UPDATE control_library
               SET required_tables = (
                     SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb) || to_jsonb(CAST('change_requests' AS text))
                       FROM jsonb_array_elements(required_tables) AS elem
                      WHERE elem <> to_jsonb(CAST('production_changes' AS text))
                   )
             WHERE control_code = 'CM-003'
               AND required_tables @> to_jsonb(CAST('production_changes' AS text))
               AND NOT (required_tables @> to_jsonb(CAST('change_requests' AS text)))
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            """
            UPDATE control_library
               SET required_tables = (
                     SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb) || to_jsonb(CAST('production_changes' AS text))
                       FROM jsonb_array_elements(required_tables) AS elem
                      WHERE elem <> to_jsonb(CAST('change_requests' AS text))
                   )
             WHERE control_code = 'CM-003'
               AND required_tables @> to_jsonb(CAST('change_requests' AS text))
               AND NOT (required_tables @> to_jsonb(CAST('production_changes' AS text)))
            """
        )
    )
    conn.execute(
        text(
            """
            UPDATE control_rule_templates
               SET rule_definition = :broken
             WHERE control_library_id IN (SELECT control_library_id FROM control_library WHERE control_code = 'CM-003')
               AND CAST(rule_definition AS jsonb) = CAST(:fixed AS jsonb)
            """
        ),
        {"broken": json.dumps(_BROKEN_DEFINITION), "fixed": json.dumps(_FIXED_DEFINITION)},
    )
