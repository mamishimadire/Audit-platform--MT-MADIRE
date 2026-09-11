"""Device command queue — admin-issued, agent-polled remote actions

Layer 2/3 of the endpoint architecture: an allowlisted set of actions
(never arbitrary execution) an admin can queue for a device, which the
agent picks up on its own next poll and reports the result of. Device
compliance policy itself piggybacks on the existing organization_settings
table (a JSON blob) rather than a new table, since it's just a handful of
booleans with no independent lifecycle of its own.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-06

"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE device_commands (
            command_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            device_id UUID NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
            command_type VARCHAR(30) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            requested_by UUID REFERENCES users(user_id) ON DELETE SET NULL,
            requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            result_message VARCHAR(500)
        )
        """
    )
    op.execute("CREATE INDEX ix_device_commands_device_status ON device_commands (device_id, status)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS device_commands")
