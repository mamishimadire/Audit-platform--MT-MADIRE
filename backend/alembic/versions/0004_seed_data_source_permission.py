"""Seed data_sources:manage permission for gateway/data-source administration

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-05

"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_PERMISSION = ("data_sources:manage", "Register gateways, data sources and connections.")
_ROLES = ["System Administrator", "Audit Manager", "IT Auditor"]


def upgrade() -> None:
    name, description = _PERMISSION
    op.execute(
        f"INSERT INTO permissions (permission_name, description) VALUES ('{name}', '{description}') "
        f"ON CONFLICT (permission_name) DO NOTHING"
    )
    for role_name in _ROLES:
        op.execute(
            "INSERT INTO role_permissions (role_id, permission_id) "
            f"SELECT r.role_id, p.permission_id FROM roles r, permissions p "
            f"WHERE r.role_name = '{role_name}' AND p.permission_name = '{name}' "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        )


def downgrade() -> None:
    name, _ = _PERMISSION
    op.execute(
        f"DELETE FROM role_permissions WHERE permission_id IN (SELECT permission_id FROM permissions WHERE permission_name = '{name}')"
    )
    op.execute(f"DELETE FROM permissions WHERE permission_name = '{name}'")
