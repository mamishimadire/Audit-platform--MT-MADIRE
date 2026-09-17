"""Let a client organization assign who owns their own exceptions/findings.

PATCH /exceptions/{id} (which sets owner_id) and POST /findings/{id}/
remediation-actions (which sets responsible_user_id) both required
audit_framework:manage — an internal-team-only permission ("create and
edit business processes, risks, controls and audit tests"). No client-
side role held it, so a client organization had no way to assign
responsibility for its own exceptions or findings at all, and it wasn't
documented anywhere who on the client side should.

This adds a narrower exceptions:assign permission, scoped to exactly
that one action, and grants it to Client Organisation Admin — the role
already responsible for administering the org and its users, so
assigning internal responsibility for a finding follows the same logic.
The routes now accept EITHER audit_framework:manage OR exceptions:assign
(see app.api.deps.require_any_permission), so no existing internal role
loses anything.

Revision ID: 0063
Revises: 0062
Create Date: 2026-09-17
"""
from alembic import op


revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO permissions (permission_name, description) VALUES "
        "('exceptions:assign', "
        "'Assign or reassign who owns an exception, or who is responsible for a finding''s remediation, within your organization.') "
        "ON CONFLICT (permission_name) DO NOTHING"
    )
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_id) "
        "SELECT r.role_id, p.permission_id FROM roles r, permissions p "
        "WHERE r.role_name = 'Client Organisation Admin' AND p.permission_name = 'exceptions:assign' "
        "ON CONFLICT (role_id, permission_id) DO NOTHING"
    )
    op.execute(
        "UPDATE roles SET description = "
        "'Manages their own organization and its users. Also the one responsible for assigning "
        "who owns an exception and who is responsible for remediating a finding within their organization.' "
        "WHERE role_name = 'Client Organisation Admin'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE roles SET description = 'Manages their own organization and its users.' "
        "WHERE role_name = 'Client Organisation Admin'"
    )
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id = "
        "(SELECT permission_id FROM permissions WHERE permission_name = 'exceptions:assign')"
    )
    op.execute("DELETE FROM permissions WHERE permission_name = 'exceptions:assign'")
