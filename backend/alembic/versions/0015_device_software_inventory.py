"""Device software inventory: one current-state row per device

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-06

"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE device_software_inventory (
            device_id UUID PRIMARY KEY REFERENCES devices(device_id) ON DELETE CASCADE,
            items JSONB NOT NULL,
            collected_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS device_software_inventory")
