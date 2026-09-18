from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_any_permission, require_permissions
from app.db.session import get_db
from app.models.rbac import Permission, Role, RolePermission
from app.schemas.role import PermissionOut, RoleOut, SodWorkflowOut

router = APIRouter(prefix="/reference", tags=["reference"])

# The full role/permission matrix and every enforced SoD rule is platform
# administration material, not something every authenticated user (down to
# Read Only) should be able to pull by navigating straight to the URL —
# same permission that gates the Administration page itself and the
# platform-wide users list (see platform_users.py).
_ADMIN_ONLY = require_permissions("organizations:manage")
# /roles is also read by Users.tsx to populate the role picker on user
# creation — users:manage covers that (held by Client Organisation Admin
# for their own org, not just Platform Admin), so it can't be as narrow
# as _ADMIN_ONLY above.
_ROLES_READER = require_any_permission("organizations:manage", "users:manage")


@router.get("/roles", response_model=list[RoleOut])
def list_roles(db: Session = Depends(get_db), _user=Depends(_ROLES_READER)) -> list[RoleOut]:
    roles = list(db.scalars(select(Role)))
    permission_rows = db.execute(
        select(RolePermission.role_id, Permission.permission_name).join(
            Permission, Permission.permission_id == RolePermission.permission_id
        )
    ).all()
    permissions_by_role: dict = {}
    for role_id, permission_name in permission_rows:
        permissions_by_role.setdefault(role_id, []).append(permission_name)

    return [
        RoleOut(
            role_id=role.role_id,
            role_name=role.role_name,
            description=role.description,
            role_scope=role.role_scope,
            account_classification="MT_AUDIT_INTERNAL" if role.role_scope == "platform" else "CLIENT_EXTERNAL",
            permissions=sorted(permissions_by_role.get(role.role_id, [])),
        )
        for role in roles
    ]


@router.get("/permissions", response_model=list[PermissionOut])
def list_permissions(db: Session = Depends(get_db), _user=Depends(_ADMIN_ONLY)) -> list[Permission]:
    return list(db.scalars(select(Permission)))


# Every maker-checker workflow actually enforced today, kept as one
# constant next to the roles/permissions endpoints above rather than
# scattered as comments across mapping_service.py/test_rule_service.py/
# control_service.py/finding_service.py/exception_service.py — this is
# the single place that must be updated whenever a workflow's states or
# enforcement rule changes, so the Administration page can never silently
# drift out of sync with what the backend actually does.
_SOD_WORKFLOWS: list[SodWorkflowOut] = [
    SodWorkflowOut(
        action="Data mapping approval",
        states=["needs_review / auto / manually_mapped", "approved", "rejected", "superseded (replaced by a later version)"],
        maker="Creates or edits a field mapping.",
        checker="Approves the mapping (POST /data-mappings/{id}/approve) or rejects it with a reason.",
        enforcement="created_by != approved_by, checked by user identity — not by role. A mapping is never execution-ready on AI confidence score alone; only 'approved' status executes. Editing an already-approved mapping creates a new version instead of changing the live one in place.",
        mandatory=True,
    ),
    SodWorkflowOut(
        action="Test rule approval",
        states=["pending_approval", "active", "rejected", "superseded (replaced by a later version)", "deleted"],
        maker="Writes or edits a test rule's logic, or generates one from a control template.",
        checker="Approves the rule (POST /test-rules/{id}/approve) or rejects it with a reason.",
        enforcement="created_by/edited_by != approved_by, checked by user identity. Only 'active' rules execute. Editing an active rule creates a new version rather than changing live test logic in place.",
        mandatory=True,
    ),
    SodWorkflowOut(
        action="Monitoring schedule approval",
        states=["pending_approval", "active", "rejected", "superseded (replaced by a later version)"],
        maker="Sets or changes a test's monitoring cadence (POST .../schedules).",
        checker="Approves the schedule (POST /schedules/{id}/approve) or rejects it with a reason.",
        enforcement="created_by != approved_by, checked by user identity. Only an 'active' schedule is ever picked up for execution. Changing the cadence on a test that already has an active schedule creates a new pending version rather than changing what's live in place.",
        mandatory=True,
    ),
    SodWorkflowOut(
        action="Control activation",
        states=["pending_mapping", "pending_activation", "active"],
        maker="Requests activation once all required tables are bound.",
        checker="Approves the activation request.",
        enforcement="activation_requested_by != activation_approved_by, checked by user identity.",
        mandatory=True,
    ),
    SodWorkflowOut(
        action="Control deactivation",
        states=["active", "pending_deactivation", "inactive"],
        maker="Requests deactivation, with a mandatory reason.",
        checker="Approves the deactivation request.",
        enforcement="deactivation_requested_by != deactivation_approved_by, checked by user identity — never skippable. A control's table bindings cannot be changed while it is active; deactivation must go through this same dual control first.",
        mandatory=True,
    ),
    SodWorkflowOut(
        action="Exception ownership assignment",
        states=["unassigned", "assigned to an owner"],
        maker="Assigns or reassigns who owns an exception (PATCH /exceptions/{id}).",
        checker="None — a single authorized user acts alone. This is a role/permission boundary, not an identity-based maker/checker dual control.",
        enforcement="Requires exceptions:assign specifically — granted only to Client Organisation Admin. Deciding who, within their own organization, is responsible for an exception is the client's own call; the internal audit team's audit_framework:manage does NOT grant this (it still covers changing an exception's status, a separate action).",
        mandatory=False,
    ),
    SodWorkflowOut(
        action="Finding remediation ownership assignment",
        states=["unassigned", "assigned to a responsible user"],
        maker="Assigns who is responsible for a finding's remediation (POST /findings/{id}/remediation-actions).",
        checker="None — a single authorized user acts alone. This is a role/permission boundary, not an identity-based maker/checker dual control.",
        enforcement="Requires audit_framework:manage (internal audit team) OR exceptions:assign (Client Organisation Admin) — unlike exception ownership, both sides can assign remediation responsibility.",
        mandatory=False,
    ),
    SodWorkflowOut(
        action="Remediation verification",
        states=["pending", "in_progress", "completed", "awaiting_retest -> closed / reopened"],
        maker="Marks a remediation action complete.",
        checker="Performs the re-test that actually closes the finding (a passed re-test is independent evidence, not a second opinion on the same claim).",
        enforcement="When the organization's SoD setting is enabled, whoever marked the remediation complete cannot also be the one who performs the re-test.",
        mandatory=False,
    ),
    SodWorkflowOut(
        action="Exception closure",
        states=["open", "awaiting_evidence", "in_progress", "resolved / closed"],
        maker="The assigned owner investigates and updates the exception's status.",
        checker="A different, authorized user closes the exception.",
        enforcement="When the organization's SoD setting is enabled, the assigned owner cannot close their own exception.",
        mandatory=False,
    ),
    SodWorkflowOut(
        action="Software classification / reclassification",
        states=["unknown / review_required", "approved / required / restricted / system_component / ignored"],
        maker="Classifies or reclassifies an installed application (POST or PATCH /organizations/{id}/approved-software) — sets the org-wide policy every enrolled device's inventory is judged against.",
        checker="None — a single authorized user acts alone. This is a role/permission boundary, not an identity-based maker/checker dual control.",
        enforcement="Requires devices:manage_policy, held only by internal roles (Platform Super Admin, Platform Admin, Audit Manager, IT/Audit Technical User, Device Manager). Deliberately excludes Client IT Admin and every other client-side role, so the organization being audited can never grade its own devices' software.",
        mandatory=True,
    ),
    SodWorkflowOut(
        action="Device revocation",
        states=["pending / online / offline", "pending_revocation", "deregistered"],
        maker="Requests revocation, with a mandatory reason (POST /devices/{id}/revocation/request). Holds devices:request_revoke: Device Manager, IT/Audit Technical User, Client IT Admin (their own org's devices only).",
        checker="Approves or rejects the revocation request. Holds devices:approve_revoke: Audit Manager, Platform Admin — never the same roles as the maker list, so a client can never quietly drop its own non-compliant device out of monitoring.",
        enforcement="revocation_requested_by != revocation_approved_by, checked by user identity, AND the maker/checker permissions are never granted to the same role — two independent layers, not one.",
        mandatory=True,
    ),
    SodWorkflowOut(
        action="Device deletion",
        states=["pending / online / offline / deregistered", "pending_deletion", "deleted (soft delete — the row and its history stay, see Deleted devices)"],
        maker="Requests deletion, with a mandatory reason (POST /devices/{id}/deletion/request). Holds devices:request_delete: Device Manager, Client IT Admin (their own org's devices only).",
        checker="Approves or rejects the deletion request. Holds devices:approve_delete: Platform Super Admin only — the single most restricted device action in the system, deliberately narrower than revocation approval. Any of the device's still-open exceptions are auto-closed on approval, since a deleted device can never be remediated.",
        enforcement="deletion_requested_by != deletion_approved_by, checked by user identity, AND devices:approve_delete is granted to no role but Platform Super Admin.",
        mandatory=True,
    ),
    SodWorkflowOut(
        action="Escalate exception to finding",
        states=["open (exception)", "escalated -> Finding created (the exception stays open and linked, it isn't consumed)"],
        maker="Escalates an open exception — including a non-compliant/restricted-software exception from AS-004, or an endpoint check failure from EP-001 — into a formal Finding (POST /exceptions/{id}/findings).",
        checker="None — a single authorized user acts alone. No second approver is required to open a Finding.",
        enforcement="Requires audit_framework:manage, held only by internal roles (Platform Super Admin, Audit Manager, Auditor, IT/Audit Technical User, Compliance Manager). No client-side role — including Client IT Admin, who manages the devices themselves — can create a Finding against their own organization's data.",
        mandatory=True,
    ),
    SodWorkflowOut(
        action="New user approval",
        states=["pending_approval", "pending (approved, awaiting the person's own activation)", "active", "rejected"],
        maker="Adds a new user to an organization or to the internal platform (POST .../users). Holds users:manage (client org) or organizations:manage (platform).",
        checker="Approves (POST .../users/{id}/approve) or rejects with a reason (POST .../users/{id}/reject) the same permission the maker used.",
        enforcement="created_by != approved_by, checked by user identity — the person who added the account can never also be the one who signs off on it. A pending_approval user cannot log in or activate their own account at all until approved.",
        mandatory=True,
    ),
    SodWorkflowOut(
        action="User deactivation",
        states=["active", "pending_deactivation", "inactive"],
        maker="Requests deactivating a user, with a mandatory reason (POST .../users/{id}/deactivation/request). Holds users:manage (client org) or organizations:manage (platform).",
        checker="Approves or rejects the request (.../deactivation/approve or /reject), the same permission the maker used.",
        enforcement="deactivation_requested_by != approved_by, checked by user identity.",
        mandatory=True,
    ),
    SodWorkflowOut(
        action="User removal",
        states=["active / inactive / pending / locked", "pending_removal", "removed (soft delete — the row and its history stay)"],
        maker="Requests removing a user, with a mandatory reason (POST .../users/{id}/removal/request). Holds users:manage (client org) or organizations:manage (platform).",
        checker="Approves or rejects the request (.../removal/approve or /reject), the same permission the maker used.",
        enforcement="removal_requested_by != approved_by, checked by user identity. A rejected removal restores whatever status the user was actually in before, not a fixed fallback.",
        mandatory=True,
    ),
]


@router.get("/sod-workflows", response_model=list[SodWorkflowOut])
def list_sod_workflows(_user=Depends(_ADMIN_ONLY)) -> list[SodWorkflowOut]:
    return _SOD_WORKFLOWS
