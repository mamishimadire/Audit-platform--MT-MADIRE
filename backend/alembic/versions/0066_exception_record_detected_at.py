"""Track when each exception_records row was actually detected.

A real_time-scheduled test re-detecting the same unresolved exception
every few minutes was adding one ExceptionRecord per run with no way to
tell them apart — the Exceptions page's "Exception records" panel just
showed every single one, piling up into dozens of near-identical blocks
for one ongoing issue. detected_at lets the page show only the latest by
default, with the rest available as history.

Existing rows are backfilled to now() — there's no way to recover their
real original detection time, but every new row from this point on gets
an accurate one.

Revision ID: 0066
Revises: 0065
Create Date: 2026-09-17
"""
from alembic import op


revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE exception_records ADD COLUMN detected_at TIMESTAMPTZ NOT NULL DEFAULT now()")


def downgrade() -> None:
    op.execute("ALTER TABLE exception_records DROP COLUMN IF EXISTS detected_at")
