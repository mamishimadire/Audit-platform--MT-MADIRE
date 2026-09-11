"""Exception remediation guidance — a "how to fix this" for every exception

Adds a free-text field populated at exception-creation time so an owner
never has to guess what "Antivirus disabled" or a failed control test
actually requires them to do next.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-06

"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE exceptions ADD COLUMN recommended_remediation TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE exceptions DROP COLUMN IF EXISTS recommended_remediation")
