"""Approved software allowlist (AS-004) — turns raw inventory into a compliance test

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-06

"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE approved_software (
            approved_software_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
            app_name VARCHAR(255) NOT NULL,
            publisher VARCHAR(255),
            approved_version_min VARCHAR(50),
            category VARCHAR(100),
            risk_level VARCHAR(20) NOT NULL DEFAULT 'medium',
            created_by UUID REFERENCES users(user_id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_approved_software_org ON approved_software (organization_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS approved_software")
