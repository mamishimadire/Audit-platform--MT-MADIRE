"""Seed Phase 3 reference data: audit-framework permission, risk categories, business-unit permission

Adds the permission that gates business unit / business process / risk /
control / audit test administration, grants it to the roles whose stated
responsibilities cover that work (build instruction Section 3 / 8), and
seeds the risk category lookup list referenced by `risks.risk_category_id`.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-05

"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_PERMISSIONS = [
    ("business_units:manage", "Create and edit business units within an organization."),
    ("audit_framework:manage", "Create and edit business processes, risks, controls and audit tests."),
]

_ROLE_PERMISSIONS = {
    "System Administrator": ["business_units:manage", "audit_framework:manage"],
    "Audit Manager": ["business_units:manage", "audit_framework:manage"],
    "Internal Auditor": ["audit_framework:manage"],
    "IT Auditor": ["audit_framework:manage"],
}

_RISK_CATEGORIES = [
    ("Financial", "Risks affecting the accuracy or integrity of financial reporting."),
    ("Operational", "Risks arising from inadequate or failed internal processes, people or systems."),
    ("Compliance", "Risks of legal or regulatory sanction, financial loss or reputational damage."),
    ("Information Technology", "Risks relating to IT systems, infrastructure and general controls."),
    ("Fraud", "Risks of intentional deception for financial or personal gain."),
    ("Cybersecurity", "Risks relating to unauthorized access, breach or compromise of systems and data."),
]


def upgrade() -> None:
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
    for name, description in _RISK_CATEGORIES:
        op.execute(
            f"INSERT INTO risk_categories (category_name, description) VALUES ('{name}', '{description}') "
            f"ON CONFLICT (category_name) DO NOTHING"
        )


def downgrade() -> None:
    category_names = ", ".join(f"'{name}'" for name, _ in _RISK_CATEGORIES)
    permission_names = ", ".join(f"'{name}'" for name, _ in _PERMISSIONS)
    op.execute(f"DELETE FROM risk_categories WHERE category_name IN ({category_names})")
    op.execute(
        f"DELETE FROM role_permissions WHERE permission_id IN (SELECT permission_id FROM permissions WHERE permission_name IN ({permission_names}))"
    )
    op.execute(f"DELETE FROM permissions WHERE permission_name IN ({permission_names})")
