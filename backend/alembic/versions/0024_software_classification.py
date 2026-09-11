"""Software classification taxonomy (AS-004 v2)

Replaces the binary approved/not-approved model with a real classification:
approved, required, restricted, system_component, ignored, review_required
(plus 'unknown' for anything with no matching policy row at all — computed,
never stored). Existing rows default to 'approved', preserving their exact
prior meaning.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-07

"""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE approved_software ADD COLUMN classification VARCHAR(20) NOT NULL DEFAULT 'approved'")


def downgrade() -> None:
    op.execute("ALTER TABLE approved_software DROP COLUMN IF EXISTS classification")
