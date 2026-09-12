"""Fix duplicate monitoring_schedules rows.

create_schedule() never checked for an existing schedule before inserting,
and the "Schedule" button in TestEnginePanel had no submit guard, so a
double-click (or the same test's mapping page being reloaded/retested
several times during QA) produced N identical active schedules for one
audit_test_id — exactly what surfaced as 7 duplicate AC-002 rows on the
Monitoring page, all "last_run: never" because only one of them could ever
plausibly be picked up per poll anyway.

This migration is the data cleanup: for each audit_test_id with more than
one active schedule, keep the oldest (earliest created_at) and deactivate
the rest (is_active = false) rather than deleting them, so their audit
trail (created_at, prior next_run history) isn't destroyed. A partial
unique index then makes it impossible for the same audit_test_id to have
more than one active schedule ever again, as a DB-level backstop alongside
the now-idempotent create_schedule() service function.

Revision ID: 0045
Revises: 0044
Create Date: 2026-09-12
"""
from alembic import op


revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE monitoring_schedules
        SET is_active = false
        WHERE is_active = true
          AND schedule_id NOT IN (
              SELECT DISTINCT ON (audit_test_id) schedule_id
              FROM monitoring_schedules
              WHERE is_active = true
              ORDER BY audit_test_id, created_at ASC
          )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_monitoring_schedules_one_active_per_test "
        "ON monitoring_schedules (audit_test_id) WHERE is_active"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_monitoring_schedules_one_active_per_test")
