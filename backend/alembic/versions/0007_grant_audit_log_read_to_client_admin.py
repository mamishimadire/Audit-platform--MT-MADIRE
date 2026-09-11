"""Grant audit_log:read to Client Organisation Admin

audit_log:read was only ever granted to platform roles (migration 0002,
pre-dating the two-tier role split in 0005). A client's own organization
admin should be able to see their own organization's audit trail — that's
exactly the kind of oversight the Audit Trail dashboard item is for.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-05

"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_ROLE = "Client Organisation Admin"
_PERMISSION = "audit_log:read"


def upgrade() -> None:
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_id) "
        f"SELECT r.role_id, p.permission_id FROM roles r, permissions p "
        f"WHERE r.role_name = '{_ROLE}' AND p.permission_name = '{_PERMISSION}' "
        "ON CONFLICT (role_id, permission_id) DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions WHERE role_id = (SELECT role_id FROM roles WHERE role_name = "
        f"'{_ROLE}') AND permission_id = (SELECT permission_id FROM permissions WHERE permission_name = '{_PERMISSION}')"
    )
