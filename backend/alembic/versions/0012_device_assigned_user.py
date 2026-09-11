"""Device fleet management: assigned user, for the professional fleet table

Adds a free-text "assigned to" field on devices — a managed laptop is
usually assigned to a named employee who may never have a platform login of
their own, so this is deliberately not a foreign key to `users`.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-06

"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE devices ADD COLUMN assigned_user_name VARCHAR(150)")


def downgrade() -> None:
    op.execute("ALTER TABLE devices DROP COLUMN IF EXISTS assigned_user_name")
