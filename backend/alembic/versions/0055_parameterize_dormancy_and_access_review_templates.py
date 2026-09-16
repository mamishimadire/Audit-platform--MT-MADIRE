"""Converts AC-003 and AC-006's existing rule templates to reference the new
configurable rule parameters (app.services.rule_parameter_service) instead
of a fixed literal day-count, demonstrating the feature end-to-end on two
already-live, already-templated controls rather than only in isolation.

AC-003 "Dormant accounts must be disabled" and AC-006 "Periodic access
reviews must occur" both hard-coded a RelativeDate of exactly -180 days
(see 0050, which first templated both using the newly-added RelativeDate
capability). Each is rewritten so relative_days is now a ParameterReference
(dormancy_days / access_review_sla_days respectively) with default=180,
multiplier=-1 — resolved entirely on the platform side before either rule
engine ever sees the rule (see execution_service.resolve_due_tests_for_
gateway / direct_execution_service._resolve_due_direct_tests), so
rule_evaluation.py and gateway/gateway/rule_engine.py need no changes.

Critically, DEFAULT_PARAMETERS["dormancy_days"] = 180 and
["access_review_sla_days"] = 180 (see rule_parameter_service.py) — chosen
to exactly match what these two templates already hard-coded, so an
organization that has never touched Rule Parameters sees IDENTICAL
behavior before and after this migration. Only a client that explicitly
overrides one of these two settings sees any change — which is the entire
point: the number becomes theirs to tune, without anyone editing or
regenerating the rule itself.

Only the templates change here, not any already-generated TestRule row —
an organization that generated its own AC-003/AC-006 rule before this
migration keeps its own copy with the old literal, exactly like every
prior rule-template migration; only rules generated AFTER this migration
pick up the parameterized version.

Schema-validated against app.schemas.test_rule's Pydantic models and
confirmed, via resolve_parameters, to produce the exact original -180
relative_days under default parameters, and a correctly-signed different
value under a simulated org override (see the resolve_parameters/
_describe_condition checks run manually before writing this migration).

Revision ID: 0055
Revises: 0054
Create Date: 2026-09-16
"""
import json

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

_UPDATES: list[tuple[str, dict]] = [
    (
        "AC-003",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "user",
            "secondary_object": "user",
            "join_field": "user_id",
            "condition_primary": {"field": "status", "operator": "eq", "value": "active"},
            "condition_secondary": {
                "field": "last_login",
                "operator": "lte",
                "value": {
                    "kind": "relative_date",
                    "relative_days": {"kind": "parameter", "key": "dormancy_days", "default": 180, "multiplier": -1},
                },
            },
        },
    ),
    (
        "AC-006",
        {
            "rule_type": "threshold",
            "object": "access_reviews",
            "field": "reviewed_at",
            "operator": "lte",
            "value": {
                "kind": "relative_date",
                "relative_days": {"kind": "parameter", "key": "access_review_sla_days", "default": 180, "multiplier": -1},
            },
        },
    ),
]

_OLD_DEFINITIONS: dict[str, dict] = {
    "AC-003": {
        "rule_type": "cross_match_condition",
        "primary_object": "user",
        "secondary_object": "user",
        "join_field": "user_id",
        "condition_primary": {"field": "status", "operator": "eq", "value": "active"},
        "condition_secondary": {
            "field": "last_login",
            "operator": "lte",
            "value": {"kind": "relative_date", "relative_days": -180},
        },
    },
    "AC-006": {
        "rule_type": "threshold",
        "object": "access_reviews",
        "field": "reviewed_at",
        "operator": "lte",
        "value": {"kind": "relative_date", "relative_days": -180},
    },
}


def upgrade() -> None:
    for control_code, rule_definition in _UPDATES:
        rule_definition_json = json.dumps(rule_definition).replace("'", "''")
        op.execute(
            f"""
            UPDATE control_rule_templates
            SET rule_definition = '{rule_definition_json}'
            WHERE control_library_id IN (SELECT control_library_id FROM control_library WHERE control_code = '{control_code}')
            """
        )


def downgrade() -> None:
    for control_code, rule_definition in _OLD_DEFINITIONS.items():
        rule_definition_json = json.dumps(rule_definition).replace("'", "''")
        op.execute(
            f"""
            UPDATE control_rule_templates
            SET rule_definition = '{rule_definition_json}'
            WHERE control_library_id IN (SELECT control_library_id FROM control_library WHERE control_code = '{control_code}')
            """
        )
