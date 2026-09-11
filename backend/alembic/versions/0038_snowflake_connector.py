"""Add Snowflake as the sixth direct-connection database engine.

Snowflake doesn't fit the generic host/port shape as cleanly as the other
five engines: it has no port at all (its dialect resolves an account
identifier into a full *.snowflakecomputing.com hostname internally), it
addresses a database AND schema (not just a database), and — the reason
this migration exists — it needs to support two fundamentally different
auth mechanisms, not just one credential shape:

  - password (existing username + encrypted_password columns, unchanged)
  - key-pair (RSA private key instead of a password — required to support
    clients whose security policy forbids password auth entirely, per the
    build instruction: "do not assume every Snowflake client allows
    username/password authentication")

Reuses `host` for the account identifier and `database_name` for the
database (both already generic enough), adds four Snowflake-specific
columns for what genuinely doesn't exist elsewhere:
  - snowflake_warehouse (required — every query needs a compute warehouse)
  - snowflake_schema (required — Snowflake addresses db.schema.table, not
    just db.table)
  - snowflake_auth_method ('password' | 'key_pair') — selects what
    encrypted_password actually holds, the same "one column, meaning
    chosen by a sibling column" pattern oracle_connection_type already
    established for database_name (Service Name vs SID)
  - snowflake_role (optional — which Snowflake role to assume)
  - encrypted_snowflake_key_passphrase (optional — a key-pair private key
    may itself be passphrase-protected)

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-09

"""
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE data_connections ADD COLUMN snowflake_warehouse VARCHAR(150)")
    op.execute("ALTER TABLE data_connections ADD COLUMN snowflake_schema VARCHAR(150)")
    op.execute("ALTER TABLE data_connections ADD COLUMN snowflake_role VARCHAR(150)")
    op.execute("ALTER TABLE data_connections ADD COLUMN snowflake_auth_method VARCHAR(20) NOT NULL DEFAULT 'password'")
    op.execute(
        "ALTER TABLE data_connections ADD CONSTRAINT data_connections_snowflake_auth_method_check "
        "CHECK (snowflake_auth_method IN ('password', 'key_pair'))"
    )
    op.execute("ALTER TABLE data_connections ADD COLUMN encrypted_snowflake_key_passphrase TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE data_connections DROP CONSTRAINT IF EXISTS data_connections_snowflake_auth_method_check")
    for column in ("snowflake_warehouse", "snowflake_schema", "snowflake_role", "snowflake_auth_method", "encrypted_snowflake_key_passphrase"):
        op.execute(f"ALTER TABLE data_connections DROP COLUMN IF EXISTS {column}")
