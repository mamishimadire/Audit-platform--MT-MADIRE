"""Add 'delete' as a third data_connection_changes.change_type, alongside
'update' and 'disconnect' (migration 0042).

Disconnect deactivates a connection but keeps its history (connection_status
= 'revoked'); delete removes the DataConnection row outright, same
maker-checker approval path. Deleting cascades to that connection's own
OAuthConnection and data_connection_changes rows (unremarkable — they have
no purpose once the parent connection is gone), but the durable audit trail
of who requested/approved the deletion and what was deleted lives in the
plain audit_logs table (via log_action), which is never touched by that
cascade.

Revision ID: 0043
Revises: 0042
Create Date: 2026-09-11
"""
from alembic import op


revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE data_connection_changes DROP CONSTRAINT IF EXISTS data_connection_changes_change_type_check")
    op.execute(
        "ALTER TABLE data_connection_changes ADD CONSTRAINT data_connection_changes_change_type_check "
        "CHECK (change_type IN ('update', 'disconnect', 'delete'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM data_connection_changes WHERE change_type = 'delete'")
    op.execute("ALTER TABLE data_connection_changes DROP CONSTRAINT IF EXISTS data_connection_changes_change_type_check")
    op.execute(
        "ALTER TABLE data_connection_changes ADD CONSTRAINT data_connection_changes_change_type_check "
        "CHECK (change_type IN ('update', 'disconnect'))"
    )
