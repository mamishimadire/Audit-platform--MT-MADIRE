"""Closes the last 4 untemplated controls (153/157 -> 157/157) by adding
one new primitive (ConflictMatrixRule) and extending an existing one
(MissingMatchRule's optional gate_object, for a 3-object anti-join).

Each of these 4 was reported as a genuine capability gap in migration
0077's docstring — re-examined here rather than left as a permanent
exception:

- RA-007 "privileged remote access requires additional approval": the
  real block was that MissingMatchRule could only anti-join 2 objects,
  and RA-007 genuinely needs a THIRD ("is this user privileged at all,
  via user_roles") gating which primary rows even get anti-joined
  against privileged_access_approvals. Added gate_object/gate_join_field/
  gate_secondary_join_field/gate_condition to MissingMatchRule: primary
  rows are INNER-joined to gate_object first (same regex-role-matching
  convention AC-005 already established), and only THOSE get anti-joined
  against secondary_object. Verified directly: a privileged user's
  session with no approval flags, the same user's approved session
  doesn't, a non-privileged user's unapproved session doesn't either
  (never gated in at all).

- SOD-007 "conflicting roles" / "compare against predefined SoD conflict
  matrix": the real block was every other rule_definition being static
  (one fixed check, authored once) while SOD-007 needs to test against
  sod_rules' OWN rows — a client can add a new conflict pair to their
  own data without anyone editing this rule. New ConflictMatrixRule
  reads every row of a rules table (rules_object) as a live list of
  permission pairs and flags any role holding both permissions of any
  pair. Verified: a role with both permissions in ANY of 2 configured
  pairs flags, one with only one side of a pair doesn't.

- RA-006 "outside approved hours/geography": re-examined rather than
  left unbuilt. The blocker was assumed to be "no safe default country
  list to guess" — but access_policies.allowed_countries is the
  CLIENT'S OWN configured value, not something this migration invents;
  BaselineComparisonRule (added in 0077) already looks up a dynamic
  value from exactly this kind of single-row policy table, it just
  didn't support "in"/"not_in" as an operator yet (only eq/ne/gt/gte/
  lt/lte). Added them. Templated the geography half only
  (remote_access_logs.source_country not_in access_policies.
  allowed_countries) — the hours half still has no engine support for
  extracting hour-of-day from a timestamp, a separate, smaller gap left
  for its own follow-up rather than blocking this control's geography
  check on it.

- WF-003 "blocked events missing from the log": re-examined rather than
  left unbuilt. web_filter_logs already had user_id; security_logs
  didn't have anything to join on at all. Added security_logs.user_id
  to the canonical model (a real audit/security log referencing which
  user triggered the event is a normal, defensible field to expect) —
  once both sides share a key, this is an ordinary missing_match: a
  blocked web_filter_logs event for a user with no security_logs entry
  at all for them.

Also fixed a real bug surfaced while re-checking MD-004 live: the
mapping_service.get_mapping_status_for_tests added in an earlier
migration this session only checked that EXISTING mappings were
approved, not that every field the rule actually reads was mapped at
all — MD-004 showed "Mapped & approved" despite payments.supplier_id
never having been mapped, because the one mapping that DID exist
(payments.payment_id) was approved. Fixed in code (no migration needed,
it's a read-time computation) to check full completeness against the
active rule's (or template's) own required fields.

Final rule-template coverage: 157/157 — every control in the library
now has one.

Revision ID: 0078
Revises: 0077
Create Date: 2026-09-18
"""
import json

from alembic import op
from sqlalchemy import text

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None

_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "RA-006",
        "Remote access session originates from a non-approved country",
        {
            "rule_type": "baseline_comparison",
            "object": "remote_access_logs",
            "field": "source_country",
            "operator": "not_in",
            "baseline_object": "access_policies",
            "baseline_value_field": "allowed_countries",
        },
    ),
    (
        "RA-007",
        "Privileged remote session has no elevated approval on file",
        {
            "rule_type": "missing_match",
            "primary_object": "remote_access_logs",
            "secondary_object": "privileged_access_approvals",
            "join_field": "session_id",
            "gate_object": "user_roles",
            "gate_join_field": "user_id",
            "gate_condition": {"field": "role", "operator": "matches", "value": "(?i)(admin|administrator|privileged|superuser|root)"},
        },
    ),
    (
        "SOD-007",
        "Role holds both permissions of a configured SoD conflict pair",
        {
            "rule_type": "conflict_matrix",
            "role_permission_object": "role_permissions",
            "role_field": "role",
            "permission_field": "permission",
            "rules_object": "sod_rules",
            "conflict_field": "conflicting_permissions",
        },
    ),
    (
        "WF-003",
        "Blocked web filter event has no matching security log entry",
        {
            "rule_type": "missing_match",
            "primary_object": "web_filter_logs",
            "secondary_object": "security_logs",
            "join_field": "user_id",
            "primary_condition": {"field": "action", "operator": "eq", "value": "blocked"},
        },
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            """
            UPDATE control_library
            SET required_tables = required_tables || to_jsonb(CAST('role_permissions' AS text))
            WHERE control_code = 'SOD-007'
              AND NOT (required_tables @> to_jsonb(CAST('role_permissions' AS text)))
            """
        )
    )

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
    bind = op.get_bind()
    bind.execute(
        text(
            """
            UPDATE control_library
            SET required_tables = (
                SELECT jsonb_agg(t) FROM jsonb_array_elements(required_tables) AS t
                WHERE t <> to_jsonb(CAST('role_permissions' AS text))
            )
            WHERE control_code = 'SOD-007'
            """
        )
    )
