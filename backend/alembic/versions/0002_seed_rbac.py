"""Seed baseline roles and permissions

Seeds the seven roles named in the build instruction (Section 8) and a
small Phase 1 permission set (organization/user/role administration, audit
log read access). Permissions for the audit-framework modules (risks,
controls, audit tests, exceptions, findings...) are added as each of those
modules is built in later phases — seeding permissions nobody can act on
yet would just be dead data.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-05

"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_ROLES = [
    ("System Administrator", "Full platform administration across all client tenants."),
    ("Audit Manager", "Manages the audit engagement for their organization: users, roles, scope."),
    ("Internal Auditor", "Reviews mappings, configures controls and audit tests, evaluates exceptions."),
    ("IT Auditor", "Technical audit focus: data sources, gateways, connections, field mappings."),
    ("Control Owner", "Investigates exceptions and findings assigned to their controls; records remediation."),
    ("Process Owner", "Owns a business process; accountable for its risks and controls."),
    ("Viewer", "Read-only access to dashboards, findings, and reports."),
]

_PERMISSIONS = [
    ("organizations:manage", "Create and edit client organizations (tenants)."),
    ("users:manage", "Create, edit and deactivate users within an organization."),
    ("roles:manage", "Assign and revoke roles for users within an organization."),
    ("audit_log:read", "View the platform audit trail."),
]

_ROLE_PERMISSIONS = {
    "System Administrator": [
        "organizations:manage",
        "users:manage",
        "roles:manage",
        "audit_log:read",
    ],
    "Audit Manager": [
        "users:manage",
        "roles:manage",
        "audit_log:read",
    ],
}


def upgrade() -> None:
    for name, description in _ROLES:
        op.execute(
            f"INSERT INTO roles (role_name, description) VALUES ('{name}', '{description}') "
            f"ON CONFLICT (role_name) DO NOTHING"
        )
    for name, description in _PERMISSIONS:
        op.execute(
            f"INSERT INTO permissions (permission_name, description) VALUES ('{name}', '{description}') "
            f"ON CONFLICT (permission_name) DO NOTHING"
        )
    for role_name, permission_names in _ROLE_PERMISSIONS.items():
        for permission_name in permission_names:
            op.execute(
                "INSERT INTO role_permissions (role_id, permission_id) "
                f"SELECT r.role_id, p.permission_id FROM roles r, permissions p "
                f"WHERE r.role_name = '{role_name}' AND p.permission_name = '{permission_name}' "
                "ON CONFLICT (role_id, permission_id) DO NOTHING"
            )


def downgrade() -> None:
    role_names = ", ".join(f"'{name}'" for name, _ in _ROLES)
    permission_names = ", ".join(f"'{name}'" for name, _ in _PERMISSIONS)
    op.execute(
        f"DELETE FROM role_permissions WHERE role_id IN (SELECT role_id FROM roles WHERE role_name IN ({role_names}))"
    )
    op.execute(f"DELETE FROM permissions WHERE permission_name IN ({permission_names})")
    op.execute(f"DELETE FROM roles WHERE role_name IN ({role_names})")
