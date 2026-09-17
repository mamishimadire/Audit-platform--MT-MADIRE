"""Maker-checker for monitoring schedules.

Creating or changing a monitoring schedule (how often, or whether, a
control gets tested) changes what's actually being monitored, so it now
goes through the same request/approve/reject shape as a test rule (see
test_rule_service.py) — a new schedule starts 'pending_approval' and is
never picked up by resolve_due_tests_for_gateway until a DIFFERENT
authorized user approves it. Changing the cadence on a test that already
has an active schedule creates a new pending version (supersedes_schedule_id
+ version) instead of mutating the live row in place, exactly like editing
an active test rule.

Existing rows are backfilled to 'active' (if is_active was already true)
or 'rejected' (if it was false) so a schedule already running for a real
organization keeps running uninterrupted — only a schedule created or
changed from this point on needs approval.

Revision ID: 0062
Revises: 0061
Create Date: 2026-09-17
"""
from alembic import op


revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE monitoring_schedules ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'pending_approval'")
    op.execute(
        "ALTER TABLE monitoring_schedules ADD CONSTRAINT monitoring_schedules_status_check "
        "CHECK (status IN ('pending_approval', 'active', 'rejected', 'superseded'))"
    )
    op.execute("ALTER TABLE monitoring_schedules ADD COLUMN created_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE monitoring_schedules ADD COLUMN approved_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE monitoring_schedules ADD COLUMN approved_at TIMESTAMPTZ")
    op.execute("ALTER TABLE monitoring_schedules ADD COLUMN rejected_reason TEXT")
    op.execute("ALTER TABLE monitoring_schedules ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    op.execute(
        "ALTER TABLE monitoring_schedules ADD COLUMN supersedes_schedule_id UUID "
        "REFERENCES monitoring_schedules(schedule_id) ON DELETE SET NULL"
    )

    op.execute(
        "UPDATE monitoring_schedules SET status = CASE WHEN is_active THEN 'active' ELSE 'rejected' END"
    )

    # migration 0045's backstop index keyed uniqueness off is_active, back
    # when that was the only "is this the live one" signal. status is now
    # the authoritative signal (a pending row can coexist with the active
    # one it would replace, both with is_active=true) — re-key the same
    # backstop to status = 'active' instead, or a pending request for a
    # test that already has an active schedule can never be inserted.
    op.execute("DROP INDEX IF EXISTS idx_monitoring_schedules_one_active_per_test")
    op.execute(
        "CREATE UNIQUE INDEX idx_monitoring_schedules_one_active_per_test "
        "ON monitoring_schedules (audit_test_id) WHERE status = 'active'"
    )

    # At most one proposed change can be pending per test — same reasoning
    # as data_connection_changes_one_pending_per_connection.
    op.execute(
        "CREATE UNIQUE INDEX monitoring_schedules_one_pending_per_test "
        "ON monitoring_schedules (audit_test_id) WHERE status = 'pending_approval'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS monitoring_schedules_one_pending_per_test")
    op.execute("DROP INDEX IF EXISTS idx_monitoring_schedules_one_active_per_test")
    op.execute(
        "CREATE UNIQUE INDEX idx_monitoring_schedules_one_active_per_test "
        "ON monitoring_schedules (audit_test_id) WHERE is_active"
    )
    op.execute("ALTER TABLE monitoring_schedules DROP COLUMN IF EXISTS supersedes_schedule_id")
    op.execute("ALTER TABLE monitoring_schedules DROP COLUMN IF EXISTS version")
    op.execute("ALTER TABLE monitoring_schedules DROP COLUMN IF EXISTS rejected_reason")
    op.execute("ALTER TABLE monitoring_schedules DROP COLUMN IF EXISTS approved_at")
    op.execute("ALTER TABLE monitoring_schedules DROP COLUMN IF EXISTS approved_by")
    op.execute("ALTER TABLE monitoring_schedules DROP COLUMN IF EXISTS created_by")
    op.execute("ALTER TABLE monitoring_schedules DROP CONSTRAINT IF EXISTS monitoring_schedules_status_check")
    op.execute("ALTER TABLE monitoring_schedules DROP COLUMN IF EXISTS status")
