"""Direct cloud database connections (no Gateway required)

Adds an alternative to the Gateway-brokered connection model: the platform
itself connects straight to a client's cloud-hosted database using
credentials entered directly. Those credentials are stored encrypted
(app/core/crypto.py) — a genuinely new credential-storage surface, since the
existing Gateway path never lets a raw credential reach this database at all.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-06

"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE data_connections ADD COLUMN connection_mode VARCHAR(20) NOT NULL DEFAULT 'gateway'")
    op.execute("ALTER TABLE data_connections ADD COLUMN db_type VARCHAR(20)")
    op.execute("ALTER TABLE data_connections ADD COLUMN host VARCHAR(255)")
    op.execute("ALTER TABLE data_connections ADD COLUMN port INTEGER")
    op.execute("ALTER TABLE data_connections ADD COLUMN database_name VARCHAR(150)")
    op.execute("ALTER TABLE data_connections ADD COLUMN username VARCHAR(150)")
    op.execute("ALTER TABLE data_connections ADD COLUMN encrypted_password TEXT")
    op.execute("ALTER TABLE data_connections ALTER COLUMN gateway_id DROP NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE data_connections DROP COLUMN IF EXISTS encrypted_password")
    op.execute("ALTER TABLE data_connections DROP COLUMN IF EXISTS username")
    op.execute("ALTER TABLE data_connections DROP COLUMN IF EXISTS database_name")
    op.execute("ALTER TABLE data_connections DROP COLUMN IF EXISTS port")
    op.execute("ALTER TABLE data_connections DROP COLUMN IF EXISTS host")
    op.execute("ALTER TABLE data_connections DROP COLUMN IF EXISTS db_type")
    op.execute("ALTER TABLE data_connections DROP COLUMN IF EXISTS connection_mode")
