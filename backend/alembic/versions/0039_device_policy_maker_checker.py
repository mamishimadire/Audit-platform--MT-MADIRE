"""Require independent approval before a device compliance policy changes.

The active policy remains the small JSON setting agents already consume. A
separate table is now necessary because a proposed policy has its own audit
lifecycle: pending approval, approved, or rejected. Only approval copies a
proposal into the live setting and triggers a re-evaluation of latest device
state.

Revision ID: 0039
Revises: 0038
Create Date: 2026-09-09
"""
from alembic import op


revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE device_policy_changes (
            policy_change_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
            proposed_policy JSONB NOT NULL,
            approval_status VARCHAR(20) NOT NULL DEFAULT 'pending_approval',
            requested_by UUID REFERENCES users(user_id) ON DELETE SET NULL,
            requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            approved_by UUID REFERENCES users(user_id) ON DELETE SET NULL,
            approved_at TIMESTAMPTZ,
            rejected_by UUID REFERENCES users(user_id) ON DELETE SET NULL,
            rejected_at TIMESTAMPTZ,
            rejected_reason TEXT,
            CONSTRAINT device_policy_changes_approval_status_check
                CHECK (approval_status IN ('pending_approval', 'approved', 'rejected'))
        )
        """
    )
    # A second draft based on an older live policy would be ambiguous, so an
    # organization has at most one policy proposal awaiting review at once.
    op.execute(
        "CREATE UNIQUE INDEX device_policy_changes_one_pending_per_organization "
        "ON device_policy_changes (organization_id) WHERE approval_status = 'pending_approval'"
    )
    op.execute(
        "INSERT INTO permissions (permission_name, description) VALUES "
        "('devices:approve_policy', 'Approve or reject a device compliance policy change proposed by another user.') "
        "ON CONFLICT (permission_name) DO NOTHING"
    )
    for role_name in ("Platform Super Admin", "Audit Manager"):
        op.execute(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT r.role_id, p.permission_id FROM roles r, permissions p "
            f"WHERE r.role_name = '{role_name}' AND p.permission_name = 'devices:approve_policy' "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS device_policy_changes_one_pending_per_organization")
    op.execute("DROP TABLE IF EXISTS device_policy_changes")
    op.execute("DELETE FROM permissions WHERE permission_name = 'devices:approve_policy'")
