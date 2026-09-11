"""Fix missing DB-level default on exceptions.last_detected_at

0014 added last_detected_at via a bare ADD COLUMN with no DEFAULT clause
(only occurrence_count got one) — the model's server_default=func.now() is
a SQLAlchemy-side hint that never reached the actual Postgres column, so
any brand-new exception (one with no existing open row to update) violated
the NOT NULL constraint on insert. The application code has been fixed to
always set last_detected_at explicitly; this migration fixes the column
itself so it's correct independent of the calling code.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-06

"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE exceptions ALTER COLUMN last_detected_at SET DEFAULT now()")


def downgrade() -> None:
    op.execute("ALTER TABLE exceptions ALTER COLUMN last_detected_at DROP DEFAULT")
