"""Structural separation for Madire's own internal organization

Adds organizations.is_internal and seeds exactly one internal org row.
Nothing existing is reassigned into it — deciding which of today's rows
(devices, gateways, users) genuinely represent Madire's own infrastructure
versus a real client's is a judgement call for a human, not something to
guess in a migration. This gives that decision a real place to land.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-06

"""
import uuid

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

INTERNAL_ORG_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.execute("ALTER TABLE organizations ADD COLUMN is_internal BOOLEAN NOT NULL DEFAULT false")
    op.execute(
        f"""
        INSERT INTO organizations (organization_id, organization_name, status, is_internal)
        VALUES ('{INTERNAL_ORG_ID}', 'Madire Internal', 'active', true)
        ON CONFLICT (organization_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM organizations WHERE organization_id = '{INTERNAL_ORG_ID}'")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS is_internal")
