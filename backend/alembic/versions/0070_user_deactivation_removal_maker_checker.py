"""Deactivating or removing a user needs a second person's sign-off too,
same dual-control principle as adding one (migration 0069) and the same
shape already used for device revocation/deletion.

Two new independent workflows, both identity-checked (requester !=
approver), both requiring users:manage (client org) or
organizations:manage (platform):

- Deactivation: active -> pending_deactivation -> inactive (approved) or
  back to active (rejected). Reversible in principle - there's no
  reactivation flow built yet, but the status itself doesn't preclude one.
- Removal: active/inactive/pending/locked -> pending_removal -> removed
  (approved) or back to its prior status (rejected). 'removed' is a soft
  delete - the row and its history (who created it, everything it ever
  did in the audit log) stay; it can never log in again.

Revision ID: 0070
Revises: 0069
Create Date: 2026-09-18
"""
from alembic import op


revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN deactivation_requested_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE users ADD COLUMN deactivation_requested_at TIMESTAMPTZ")
    op.execute("ALTER TABLE users ADD COLUMN deactivation_reason TEXT")
    op.execute("ALTER TABLE users ADD COLUMN removal_requested_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE users ADD COLUMN removal_requested_at TIMESTAMPTZ")
    op.execute("ALTER TABLE users ADD COLUMN removal_reason TEXT")
    # Removal can be requested from more than one prior status (active,
    # inactive, pending, locked) — unlike a device, a user has no
    # self-correcting heartbeat to fall back on, so rejecting a removal
    # request has to actually restore whatever it was before, not just
    # a fixed fallback value.
    op.execute("ALTER TABLE users ADD COLUMN removal_prior_status VARCHAR(20)")

    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_status_check")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT users_status_check "
        "CHECK (status IN ("
        "'pending_approval', 'pending', 'active', 'inactive', 'locked', 'rejected', "
        "'pending_deactivation', 'pending_removal', 'removed'"
        "))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_status_check")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT users_status_check "
        "CHECK (status IN ('pending_approval', 'pending', 'active', 'inactive', 'locked', 'rejected'))"
    )
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS removal_prior_status")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS removal_reason")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS removal_requested_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS removal_requested_by")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS deactivation_reason")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS deactivation_requested_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS deactivation_requested_by")
