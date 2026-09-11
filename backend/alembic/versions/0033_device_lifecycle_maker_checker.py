"""Device revocation and deletion require a second approver, and deletion
becomes a soft delete so a decommissioned device's history is answerable.

Two problems this closes:
  1. Revoking or deleting a device was a single click by anyone holding
     devices:manage — unlike every other consequential lifecycle action in
     this platform (control activation/deactivation, mapping/rule
     approval), there was no dual control on "take this device out of
     scope." A single compromised or careless account could quietly drop
     a non-compliant device from monitoring.
  2. Deletion was a hard delete (see the old delete_device docstring) — so
     "which devices were deleted, when, and why" had no answer beyond
     free-text audit_logs rows. Deletion is now a status on the row itself
     (soft delete), so a deleted device's identity, org, and reason stay
     queryable like any other record instead of just existing as prose in
     a log.

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-08

"""
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE devices DROP CONSTRAINT IF EXISTS devices_status_check")
    op.execute(
        "ALTER TABLE devices ADD CONSTRAINT devices_status_check "
        "CHECK (status IN ('pending', 'online', 'offline', 'deregistered', "
        "'pending_revocation', 'pending_deletion', 'deleted'))"
    )

    op.execute("ALTER TABLE devices ADD COLUMN revocation_requested_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE devices ADD COLUMN revocation_requested_at TIMESTAMPTZ")
    op.execute("ALTER TABLE devices ADD COLUMN revocation_reason TEXT")
    op.execute("ALTER TABLE devices ADD COLUMN revocation_approved_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE devices ADD COLUMN revocation_approved_at TIMESTAMPTZ")

    op.execute("ALTER TABLE devices ADD COLUMN deletion_requested_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE devices ADD COLUMN deletion_requested_at TIMESTAMPTZ")
    op.execute("ALTER TABLE devices ADD COLUMN deletion_reason TEXT")
    op.execute("ALTER TABLE devices ADD COLUMN deletion_approved_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE devices ADD COLUMN deletion_approved_at TIMESTAMPTZ")


def downgrade() -> None:
    for column in (
        "revocation_requested_by", "revocation_requested_at", "revocation_reason",
        "revocation_approved_by", "revocation_approved_at",
        "deletion_requested_by", "deletion_requested_at", "deletion_reason",
        "deletion_approved_by", "deletion_approved_at",
    ):
        op.execute(f"ALTER TABLE devices DROP COLUMN IF EXISTS {column}")

    op.execute("ALTER TABLE devices DROP CONSTRAINT IF EXISTS devices_status_check")
    op.execute(
        "ALTER TABLE devices ADD CONSTRAINT devices_status_check "
        "CHECK (status IN ('pending', 'online', 'offline', 'deregistered'))"
    )
