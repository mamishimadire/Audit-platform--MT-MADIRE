"""Grant Platform Super Admin devices:manage and devices:manage_policy

Discovered live: the platform's own top role could not enroll/revoke
devices or manage device policy at all — an original seed-data gap, not a
new restriction. Platform Super Admin is meant to bypass tenant scoping
entirely (see tenant_scope_service.can_access_organization); it should
never be blocked by an ordinary permission check either.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-06

"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_PERMISSIONS = ("devices:manage", "devices:manage_policy")


def upgrade() -> None:
    for permission_name in _PERMISSIONS:
        op.execute(
            f"""
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT r.role_id, p.permission_id
            FROM roles r, permissions p
            WHERE r.role_name = 'Platform Super Admin' AND p.permission_name = '{permission_name}'
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        )


def downgrade() -> None:
    for permission_name in _PERMISSIONS:
        op.execute(
            f"""
            DELETE FROM role_permissions
            WHERE role_id = (SELECT role_id FROM roles WHERE role_name = 'Platform Super Admin')
            AND permission_id = (SELECT permission_id FROM permissions WHERE permission_name = '{permission_name}')
            """
        )
