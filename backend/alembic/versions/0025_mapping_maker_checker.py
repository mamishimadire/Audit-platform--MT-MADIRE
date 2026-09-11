"""Track who created a data mapping, for maker-checker on approval

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-07

"""
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE test_data_mappings ADD COLUMN created_by UUID REFERENCES users(user_id) ON DELETE SET NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE test_data_mappings DROP COLUMN IF EXISTS created_by")
