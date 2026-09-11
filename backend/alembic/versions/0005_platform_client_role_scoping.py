"""Two-tier RBAC: platform users vs. client users, with org-scoped platform access

At small scale, a platform-level user (organization_id IS NULL on `users`)
having unrestricted access to every client organization was harmless. It
stops being harmless at "thousands of clients": an auditor needs access to
their assigned 30 clients, not all 10,000, and a platform Support User
should not automatically inherit full audit access either.

This migration:
  1. Adds `roles.role_scope` ('platform' | 'client') so the role list itself
     enforces which security domain a role belongs to — a client user can
     only ever hold a client-scoped role and vice versa (enforced in
     app.services.user_service / app.api.deps, not just by convention).
  2. Adds `user_organization_scope`: which specific client organizations a
     platform user may access. A client user never needs a row here — their
     own `organization_id` is already their one and only scope. Only the
     'Platform Super Admin' role bypasses this table entirely.
  3. Renames the existing 7 roles in place (preserving role_id, so existing
     role_permissions and user_roles — including the real System
     Administrator account — carry over unchanged) into the two-tier set,
     and adds the remaining roles from that set.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-05

"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# (old_name, new_name, new_scope, new_description)
_RENAMES = [
    ("System Administrator", "Platform Super Admin", "platform", "Complete platform administration; bypasses per-client scoping entirely."),
    ("Audit Manager", "Audit Manager", "platform", "Manages audit engagements/tests for their assigned client organizations."),
    ("Internal Auditor", "Auditor", "platform", "Performs and reviews audit work for their assigned client organizations."),
    ("IT Auditor", "IT/Audit Technical User", "platform", "Manages gateways and data connections for their assigned client organizations."),
    ("Control Owner", "Control Owner", "client", "Responsible for assigned controls within their own organization."),
    ("Process Owner", "Process Owner", "client", "Responsible for assigned business processes within their own organization."),
    ("Viewer", "Read Only", "client", "Read-only access to permitted information within their own organization."),
]

_NEW_ROLES = [
    ("Platform Admin", "platform", "Manages client organizations and platform configuration."),
    ("Support User", "platform", "Supports clients without unnecessary audit access."),
    ("Compliance Manager", "platform", "Manages frameworks and the control/audit-test library."),
    ("Client Organisation Admin", "client", "Manages their own organization and its users."),
    ("Client IT Admin", "client", "Manages systems, gateways and connections for their own organization."),
    ("Exception Owner", "client", "Resolves exceptions assigned to them within their own organization."),
    ("Reviewer", "client", "Reviews evidence and exceptions within their own organization."),
    ("Executive", "client", "Dashboard and reporting access within their own organization."),
]

_NEW_ROLE_PERMISSIONS = {
    "Platform Admin": ["organizations:manage", "users:manage", "roles:manage", "audit_log:read"],
    "Support User": ["audit_log:read"],
    "Compliance Manager": ["audit_framework:manage"],
    "Client Organisation Admin": ["users:manage", "business_units:manage"],
    "Client IT Admin": ["data_sources:manage"],
}


def upgrade() -> None:
    op.execute("ALTER TABLE roles ADD COLUMN role_scope VARCHAR(20) NOT NULL DEFAULT 'client'")

    op.execute(
        """
        CREATE TABLE user_organization_scope (
            scope_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
            granted_by      UUID REFERENCES users(user_id) ON DELETE SET NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (user_id, organization_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_user_org_scope_user ON user_organization_scope(user_id)")
    op.execute("CREATE INDEX idx_user_org_scope_org ON user_organization_scope(organization_id)")

    for old_name, new_name, scope, description in _RENAMES:
        op.execute(
            f"UPDATE roles SET role_name = '{new_name}', role_scope = '{scope}', description = '{description}' "
            f"WHERE role_name = '{old_name}'"
        )

    for name, scope, description in _NEW_ROLES:
        op.execute(
            f"INSERT INTO roles (role_name, role_scope, description) VALUES ('{name}', '{scope}', '{description}') "
            f"ON CONFLICT (role_name) DO NOTHING"
        )

    for role_name, permission_names in _NEW_ROLE_PERMISSIONS.items():
        for permission_name in permission_names:
            op.execute(
                "INSERT INTO role_permissions (role_id, permission_id) "
                f"SELECT r.role_id, p.permission_id FROM roles r, permissions p "
                f"WHERE r.role_name = '{role_name}' AND p.permission_name = '{permission_name}' "
                "ON CONFLICT (role_id, permission_id) DO NOTHING"
            )


def downgrade() -> None:
    new_role_names = ", ".join(f"'{name}'" for name, _, _ in _NEW_ROLES)
    op.execute(f"DELETE FROM role_permissions WHERE role_id IN (SELECT role_id FROM roles WHERE role_name IN ({new_role_names}))")
    op.execute(f"DELETE FROM roles WHERE role_name IN ({new_role_names})")

    for old_name, new_name, _scope, _description in _RENAMES:
        op.execute(f"UPDATE roles SET role_name = '{old_name}' WHERE role_name = '{new_name}'")

    op.execute("DROP TABLE IF EXISTS user_organization_scope")
    op.execute("ALTER TABLE roles DROP COLUMN IF EXISTS role_scope")
