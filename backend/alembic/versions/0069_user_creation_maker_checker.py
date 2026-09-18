"""Adding a user needs a second person's sign-off, same as every other
privileged action in this system.

Creating a client-org user or an internal platform user was previously a
single-step action — the account existed and could be activated the
moment one admin created it, with nobody else ever looking at it. This
adds the same maker-checker shape already used for test rules, control
activation, monitoring schedules, etc.: a new user starts life
'pending_approval', and a DIFFERENT authorized user (checked by identity,
not just permission) must approve it before it can even be activated.
'pending' now means "approved, and now just waiting on the person
themselves to set their own password" — the meaning it always had, one
step later in the lifecycle. Rejecting a pending user is also supported,
mirroring rule/control/schedule rejection.

Revision ID: 0069
Revises: 0068
Create Date: 2026-09-18
"""
from alembic import op


revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN created_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE users ADD COLUMN approved_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE users ADD COLUMN approved_at TIMESTAMPTZ")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_status_check")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT users_status_check "
        "CHECK (status IN ('pending_approval', 'pending', 'active', 'inactive', 'locked', 'rejected'))"
    )
    op.execute("ALTER TABLE users ALTER COLUMN status SET DEFAULT 'pending_approval'")
    # Every user that already exists was created before this workflow —
    # treating them as already-approved is the only sensible backfill,
    # not a workaround: there's no real "who approved this" to recover.
    op.execute("UPDATE users SET approved_at = created_at WHERE status IN ('pending', 'active') AND approved_at IS NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN status SET DEFAULT 'pending'")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_status_check")
    op.execute("ALTER TABLE users ADD CONSTRAINT users_status_check CHECK (status IN ('pending','active','inactive','locked'))")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS approved_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS approved_by")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS created_by")
