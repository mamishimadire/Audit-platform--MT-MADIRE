"""Exception deduplication: one row per ongoing issue, not one per test run

An unresolved exception was getting a brand-new row every time the same
underlying condition was re-detected (e.g. every ~5-minute endpoint agent
heartbeat while a device stays non-compliant) — 7 identical rows for one
real problem. `occurrence_count` / `last_detected_at` let a single open
exception track "still happening, most recently at X" without duplicating,
while `detected_at` keeps its original meaning: first ever detection.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-06

"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE exceptions ADD COLUMN occurrence_count INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE exceptions ADD COLUMN last_detected_at TIMESTAMPTZ")
    op.execute("UPDATE exceptions SET last_detected_at = detected_at WHERE last_detected_at IS NULL")
    op.execute("ALTER TABLE exceptions ALTER COLUMN last_detected_at SET NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE exceptions DROP COLUMN IF EXISTS last_detected_at")
    op.execute("ALTER TABLE exceptions DROP COLUMN IF EXISTS occurrence_count")
