"""Software classification requires a second, independent approval before it
takes effect — closing the same gap the platform already closed for
mappings, test rules, and device revocation/deletion: one person's decision
must never silently become the live control on its own.

Before this migration, devices:manage_policy let a single person classify
an app (e.g. mark "RemoteAccessTool.exe" as Approved) and that decision
took effect on the very next compliance check — no review, no second
opinion, even though a bad classification call directly changes which
devices show as compliant.

This migration adds an `approval_status` workflow to `approved_software`,
deliberately separate from the existing `classification` column (which
still holds the actual value — approved/required/restricted/
system_component/ignored/review_required):

  pending_approval -> approved | rejected
  approved (edited) -> a NEW pending_approval row (version+1, supersedes_id
                        pointing back) — the OLD row stays 'approved' and
                        keeps enforcing until the new one is itself approved,
                        exactly like editing an active test rule.

Every existing row is backfilled to 'approved' — this is a forward-looking
control on the classification process, not a retroactive audit of
everything already classified.

Identity check enforced in software_compliance_service.py:
classified_by (created_by) != approved_by, same mechanism as every other
maker-checker workflow in this platform.

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-09

"""
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE approved_software ADD COLUMN approval_status VARCHAR(20) NOT NULL DEFAULT 'pending_approval'")
    op.execute(
        "ALTER TABLE approved_software ADD CONSTRAINT approved_software_approval_status_check "
        "CHECK (approval_status IN ('pending_approval', 'approved', 'rejected', 'superseded'))"
    )
    op.execute("ALTER TABLE approved_software ADD COLUMN approved_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE approved_software ADD COLUMN approved_at TIMESTAMPTZ")
    op.execute("ALTER TABLE approved_software ADD COLUMN rejected_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE approved_software ADD COLUMN rejected_at TIMESTAMPTZ")
    op.execute("ALTER TABLE approved_software ADD COLUMN rejected_reason TEXT")
    op.execute("ALTER TABLE approved_software ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE approved_software ADD COLUMN supersedes_id UUID REFERENCES approved_software(approved_software_id) ON DELETE SET NULL")

    # Backfill: everything classified before this control existed is
    # grandfathered in as already-approved, so existing device compliance
    # results don't change the moment this migration runs.
    op.execute("UPDATE approved_software SET approval_status = 'approved', approved_at = updated_at WHERE approval_status = 'pending_approval'")


def downgrade() -> None:
    op.execute("ALTER TABLE approved_software DROP CONSTRAINT IF EXISTS approved_software_approval_status_check")
    for column in ("approval_status", "approved_by", "approved_at", "rejected_by", "rejected_at", "rejected_reason", "version", "supersedes_id"):
        op.execute(f"ALTER TABLE approved_software DROP COLUMN IF EXISTS {column}")
