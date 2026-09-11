"""Device Manager role — platform-scoped, for testing/assigning device policy & remote-command access

Requested to let someone other than a Platform Super Admin test the
devices:manage / devices:manage_policy gated features without granting a
much broader role. Platform-scoped (not client-scoped) — consistent with
the segregation-of-duties fix in 0020: a client should never hold this.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-06

"""
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_PERMISSIONS = ("devices:manage", "devices:manage_policy")


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO roles (role_name, role_scope, description)
        VALUES ('Device Manager', 'platform', 'Manages enrolled devices: enrollment/revocation, compliance policy, and remote device commands (restart, run check now).')
        ON CONFLICT (role_name) DO NOTHING
        """
    )
    for permission_name in _PERMISSIONS:
        op.execute(
            f"""
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT r.role_id, p.permission_id
            FROM roles r, permissions p
            WHERE r.role_name = 'Device Manager' AND p.permission_name = '{permission_name}'
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE role_id = (SELECT role_id FROM roles WHERE role_name = 'Device Manager')
        """
    )
    op.execute("DELETE FROM roles WHERE role_name = 'Device Manager'")
