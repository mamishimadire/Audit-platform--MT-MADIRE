"""Separate permission for device compliance policy & remote commands

devices:manage (enroll/revoke a device) is legitimately held by Client IT
Admin — that's the client's own job per the product spec. But *setting the
compliance policy that judges their own devices* and *issuing remote
actions like restart* are auditor/platform-side judgement calls, not
something the audited party should control unsupervised — the same
principle as segregation of duties elsewhere in this platform. This adds a
distinct devices:manage_policy permission held by the platform-side roles
that already had devices:manage, with Client IT Admin deliberately excluded.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-06

"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_GRANTED_ROLES = ("Platform Admin", "Audit Manager", "IT/Audit Technical User")


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO permissions (permission_name, description)
        VALUES ('devices:manage_policy', 'Set device compliance policy and issue remote device commands (restart, run check now)')
        ON CONFLICT (permission_name) DO NOTHING
        """
    )
    for role_name in _GRANTED_ROLES:
        op.execute(
            f"""
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT r.role_id, p.permission_id
            FROM roles r, permissions p
            WHERE r.role_name = '{role_name}' AND p.permission_name = 'devices:manage_policy'
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_id = (SELECT permission_id FROM permissions WHERE permission_name = 'devices:manage_policy')
        """
    )
    op.execute("DELETE FROM permissions WHERE permission_name = 'devices:manage_policy'")
