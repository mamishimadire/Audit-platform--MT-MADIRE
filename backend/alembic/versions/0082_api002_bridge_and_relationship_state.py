"""API-002 follows the bridge table, and Gateway connections remember when their
relationships were last measured.

1. API-002 ("API access must be authorised"). Its template only checked that the
   grant's user exists and is active (`api_access.user_id -> user`). Authorisation
   also means the user HOLDS A ROLE, and that evidence is two hops away
   (api_access -> user_roles -> user), which `missing_match` could not follow. It
   now can (`bridge_*` fields on missing_match, executed by both rule engines), so
   the template becomes:

       flag an api_access grant unless its user appears in user_roles AND is an
       ACTIVE user

   Today's finding is kept (an inactive or missing user's grant is still flagged,
   e.g. the demo's contractor U012) and the authorisation dimension is added (an
   active user with no role at all is flagged too).

   `user_roles` is added to API-002's required_tables so it can be bound. The
   template is only replaced when it is still exactly the definition seeded in
   0076, so a later, deliberate edit is never overwritten. No test rule has been
   generated for API-002 from the old template in any live organisation, so nothing
   running changes; the control just needs one more table bound (for an ACTIVE
   control that means deactivate, bind, reactivate, like any binding change).

2. `data_connections.relationships_measured_at`: when a Gateway connection last
   reported measured relationships (see the relationship-requests channel). NULL
   means "never" and also "please measure again".

Additive apart from the one guarded template UPDATE.

Revision ID: 0082
Revises: 0081
Create Date: 2026-09-19
"""
import json

from alembic import op
from sqlalchemy import text

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None

_OLD_NAME = "API access grant belongs to an inactive or missing user"
_OLD_DEFINITION = {
    "rule_type": "missing_match",
    "primary_object": "api_access",
    "secondary_object": "user",
    "join_field": "user_id",
    "secondary_condition": {"field": "status", "operator": "eq", "value": "active"},
}
_NEW_NAME = "API access grant belongs to an inactive user, or to a user who holds no role"
_NEW_DEFINITION = {
    "rule_type": "missing_match",
    "primary_object": "api_access",
    "join_field": "user_id",
    "bridge_object": "user_roles",
    "bridge_join_field": "user_id",
    "bridge_secondary_join_field": "user_id",
    "secondary_object": "user",
    "secondary_join_field": "user_id",
    "secondary_condition": {"field": "status", "operator": "eq", "value": "active"},
}


def _swap_template(conn, *, from_name, from_definition, to_name, to_definition) -> None:
    conn.execute(
        text(
            """
            UPDATE control_rule_templates
               SET rule_name = :to_name, rule_definition = :to_definition
             WHERE control_library_id IN (SELECT control_library_id FROM control_library WHERE control_code = 'API-002')
               AND rule_name = :from_name
               AND CAST(rule_definition AS jsonb) = CAST(:from_definition AS jsonb)
            """
        ),
        {
            "to_name": to_name,
            "to_definition": json.dumps(to_definition),
            "from_name": from_name,
            "from_definition": json.dumps(from_definition),
        },
    )


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE data_connections ADD COLUMN IF NOT EXISTS relationships_measured_at TIMESTAMPTZ"))
    conn.execute(
        text(
            """
            UPDATE control_library
               SET required_tables = required_tables || to_jsonb(CAST(:table_to_add AS text))
             WHERE control_code = 'API-002'
               AND NOT (required_tables @> to_jsonb(CAST(:table_to_add AS text)))
            """
        ),
        {"table_to_add": "user_roles"},
    )
    _swap_template(conn, from_name=_OLD_NAME, from_definition=_OLD_DEFINITION, to_name=_NEW_NAME, to_definition=_NEW_DEFINITION)


def downgrade() -> None:
    conn = op.get_bind()
    _swap_template(conn, from_name=_NEW_NAME, from_definition=_NEW_DEFINITION, to_name=_OLD_NAME, to_definition=_OLD_DEFINITION)
    conn.execute(
        text(
            """
            UPDATE control_library
               SET required_tables = COALESCE(
                     (SELECT jsonb_agg(elem) FROM jsonb_array_elements(required_tables) AS elem WHERE elem <> to_jsonb(CAST(:table_to_remove AS text))),
                     '[]'::jsonb)
             WHERE control_code = 'API-002'
            """
        ),
        {"table_to_remove": "user_roles"},
    )
    conn.execute(text("ALTER TABLE data_connections DROP COLUMN IF EXISTS relationships_measured_at"))
