"""Hide/unhide for discovered tables and connections, plus maker-checker
for editing or disconnecting a data connection.

Hiding is a pure display toggle (no approval, see DataConnection.is_hidden /
DataEntity.is_hidden docstrings). Editing a connection's host/credentials/
name, or disconnecting it, changes what the platform actually monitors, so
those go through the same request/approve/reject shape as device policy
changes (migration 0039) — a new data_connection_changes table, and a new
data_sources:approve_change permission separate from data_sources:manage
(the requester's own permission), so the approver must be a different,
independently-authorized user.

Revision ID: 0042
Revises: 0041
Create Date: 2026-09-11
"""
from alembic import op


revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE data_connections ADD COLUMN connection_name VARCHAR(150)")
    op.execute("ALTER TABLE data_connections ADD COLUMN is_hidden BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE data_entities ADD COLUMN is_hidden BOOLEAN NOT NULL DEFAULT false")

    op.execute(
        """
        CREATE TABLE data_connection_changes (
            change_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            connection_id     UUID NOT NULL REFERENCES data_connections(connection_id) ON DELETE CASCADE,
            change_type       VARCHAR(20) NOT NULL CHECK (change_type IN ('update', 'disconnect')),
            proposed_changes  JSONB NOT NULL,
            approval_status   VARCHAR(20) NOT NULL DEFAULT 'pending_approval'
                               CHECK (approval_status IN ('pending_approval', 'approved', 'rejected')),
            requested_by      UUID REFERENCES users(user_id) ON DELETE SET NULL,
            requested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            approved_by       UUID REFERENCES users(user_id) ON DELETE SET NULL,
            approved_at       TIMESTAMPTZ,
            rejected_by       UUID REFERENCES users(user_id) ON DELETE SET NULL,
            rejected_at       TIMESTAMPTZ,
            rejected_reason   TEXT
        )
        """
    )
    # A second draft against the same connection would be ambiguous, so at
    # most one proposed change can be pending per connection — same
    # reasoning as device_policy_changes_one_pending_per_organization.
    op.execute(
        "CREATE UNIQUE INDEX data_connection_changes_one_pending_per_connection "
        "ON data_connection_changes (connection_id) WHERE approval_status = 'pending_approval'"
    )
    op.execute(
        "INSERT INTO permissions (permission_name, description) VALUES "
        "('data_sources:approve_change', "
        "'Approve or reject a data connection edit/disconnect requested by another user.') "
        "ON CONFLICT (permission_name) DO NOTHING"
    )
    for role_name in ("Platform Super Admin", "Audit Manager"):
        op.execute(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT r.role_id, p.permission_id FROM roles r, permissions p "
            f"WHERE r.role_name = '{role_name}' AND p.permission_name = 'data_sources:approve_change' "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        )


def downgrade() -> None:
    op.execute("DELETE FROM permissions WHERE permission_name = 'data_sources:approve_change'")
    op.execute("DROP INDEX IF EXISTS data_connection_changes_one_pending_per_connection")
    op.execute("DROP TABLE IF EXISTS data_connection_changes")
    op.execute("ALTER TABLE data_entities DROP COLUMN IF EXISTS is_hidden")
    op.execute("ALTER TABLE data_connections DROP COLUMN IF EXISTS is_hidden")
    op.execute("ALTER TABLE data_connections DROP COLUMN IF EXISTS connection_name")
