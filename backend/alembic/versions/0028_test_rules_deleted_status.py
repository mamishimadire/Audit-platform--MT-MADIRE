"""Allow 'deleted' as a test_rules.status value (soft-delete, never hard-delete)

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-07

"""
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE test_rules DROP CONSTRAINT test_rules_status_check")
    op.execute(
        "ALTER TABLE test_rules ADD CONSTRAINT test_rules_status_check "
        "CHECK (status IN ('active', 'inactive', 'deleted'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE test_rules DROP CONSTRAINT test_rules_status_check")
    op.execute(
        "ALTER TABLE test_rules ADD CONSTRAINT test_rules_status_check "
        "CHECK (status IN ('active', 'inactive'))"
    )
