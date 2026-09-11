"""Replace the single devices:manage permission with a granular set that
separates operational (maker) actions from approval (checker) actions.

Rationale (see the full design discussion): devices:manage let one person
enroll, request AND approve their own revocation/deletion, and issue
disruptive commands unchecked. That's a real audit-population integrity
gap — an audited organization's own Client IT Admin could otherwise
silently drop a non-compliant device out of monitoring. This migration:

  1. Adds 9 new permissions replacing devices:manage's scope: view, enrol,
     request_revoke, approve_revoke, request_delete, approve_delete,
     command_safe, command_disruptive, approve_disruptive_command.
  2. Grants them per role, deliberately narrower than devices:manage was:
     - Device Manager / IT-Audit Technical User / Client IT Admin can
       REQUEST revocation/deletion but never approve their own domain.
     - Only Audit Manager and Platform Super Admin can approve a
       revocation. Only Platform Super Admin can approve a deletion
       ("final purge" — the most consequential, most restricted action).
  3. Deletes the old devices:manage permission (cascades its
     role_permissions grants).

Two of the nine (devices:view, devices:approve_disruptive_command) are
catalogued here but not yet enforced on any route — see the routes.py
change in this same commit for exactly which routes now check which new
permission, and the code comments there for what's deliberately deferred.

devices:manage_policy is UNCHANGED by this migration — it still gates the
compliance-policy toggle and the approved-software (classification)
routes, which are explicitly the next, separate phase of this redesign
(a maker-checker classification workflow), not folded in here.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-09

"""
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_NEW_PERMISSIONS = {
    "devices:view": "View devices and their status.",
    "devices:enrol": "Register/enroll a new device.",
    "devices:request_revoke": "Request that a device be revoked — a different, authorized user must approve it.",
    "devices:approve_revoke": "Approve or reject a device revocation request.",
    "devices:request_delete": "Request permanent deletion of a device — a different, authorized user must approve it.",
    "devices:approve_delete": "Approve or reject a device deletion request (final purge — the most restricted device action).",
    "devices:command_safe": "Issue a non-disruptive remote device command (e.g. run compliance check now).",
    "devices:command_disruptive": "Request a disruptive remote device command (e.g. restart).",
    "devices:approve_disruptive_command": "Approve a disruptive remote device command before it reaches the device.",
}

# role_name -> permission_names granted
_ROLE_GRANTS = {
    "Platform Super Admin": list(_NEW_PERMISSIONS),
    "Platform Admin": ["devices:view", "devices:enrol", "devices:request_revoke", "devices:approve_revoke", "devices:request_delete", "devices:command_safe", "devices:command_disruptive"],
    "Audit Manager": ["devices:view", "devices:approve_revoke", "devices:approve_disruptive_command"],
    "IT/Audit Technical User": ["devices:view", "devices:request_revoke", "devices:command_safe"],
    "Device Manager": ["devices:view", "devices:enrol", "devices:request_revoke", "devices:request_delete", "devices:command_safe", "devices:command_disruptive"],
    "Client IT Admin": ["devices:enrol", "devices:request_revoke", "devices:request_delete", "devices:command_safe", "devices:command_disruptive"],
}


def upgrade() -> None:
    for name, description in _NEW_PERMISSIONS.items():
        op.execute(
            f"INSERT INTO permissions (permission_name, description) VALUES ('{name}', '{description}') "
            "ON CONFLICT (permission_name) DO NOTHING"
        )

    for role_name, permission_names in _ROLE_GRANTS.items():
        for permission_name in permission_names:
            op.execute(
                "INSERT INTO role_permissions (role_id, permission_id) "
                f"SELECT r.role_id, p.permission_id FROM roles r, permissions p "
                f"WHERE r.role_name = '{role_name}' AND p.permission_name = '{permission_name}' "
                "ON CONFLICT (role_id, permission_id) DO NOTHING"
            )

    op.execute("DELETE FROM permissions WHERE permission_name = 'devices:manage'")


def downgrade() -> None:
    op.execute(
        "INSERT INTO permissions (permission_name, description) VALUES "
        "('devices:manage', 'Enroll and manage endpoint devices.') ON CONFLICT (permission_name) DO NOTHING"
    )
    for role_name in ("Platform Super Admin", "Platform Admin", "Audit Manager", "IT/Audit Technical User", "Device Manager", "Client IT Admin"):
        op.execute(
            "INSERT INTO role_permissions (role_id, permission_id) "
            f"SELECT r.role_id, p.permission_id FROM roles r, permissions p "
            f"WHERE r.role_name = '{role_name}' AND p.permission_name = 'devices:manage' "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        )
    permission_names = ", ".join(f"'{name}'" for name in _NEW_PERMISSIONS)
    op.execute(f"DELETE FROM permissions WHERE permission_name IN ({permission_names})")
