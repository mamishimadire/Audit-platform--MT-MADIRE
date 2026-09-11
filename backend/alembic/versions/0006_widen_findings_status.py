"""Widen findings.status — the original column was too narrow for its own values

schema.sql defined `findings.status VARCHAR(20)` with a CHECK constraint
that allows 'remediation_in_progress' (24 characters) — a genuine sizing
bug from the original schema design, caught by a live insert failing with
StringDataRightTruncation the first time a finding actually transitioned
into that status. Widened to VARCHAR(30), comfortably above the longest
allowed value.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-05

"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE findings ALTER COLUMN status TYPE VARCHAR(30)")


def downgrade() -> None:
    op.execute("ALTER TABLE findings ALTER COLUMN status TYPE VARCHAR(20)")
