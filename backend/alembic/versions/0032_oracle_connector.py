"""Add Oracle as a fourth direct-connection database engine.

Oracle is the one relational engine here that doesn't fit the generic
"driver://user:pass@host:port/database" URL shape every other engine
uses — it needs to know whether the trailing identifier is a Service
Name (goes in as a query param) or a SID (goes in the path, same as
every other engine's database name). This column is the only schema
change Oracle needs; schema discovery itself needs none, since
SQLAlchemy's oracle dialect implements table/column reflection against
ALL_TABLES/ALL_TAB_COLUMNS internally — inspect() already works.

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-08

"""
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE data_connections ADD COLUMN oracle_connection_type VARCHAR(20)")
    op.execute(
        "ALTER TABLE data_connections ADD CONSTRAINT data_connections_oracle_connection_type_check "
        "CHECK (oracle_connection_type IS NULL OR oracle_connection_type IN ('service_name', 'sid'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE data_connections DROP CONSTRAINT IF EXISTS data_connections_oracle_connection_type_check")
    op.execute("ALTER TABLE data_connections DROP COLUMN IF EXISTS oracle_connection_type")
