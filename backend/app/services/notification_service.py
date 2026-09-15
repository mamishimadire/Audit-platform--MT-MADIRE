import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_test import AuditTest, TestRule
from app.models.data_source import DataConnection, DataConnectionChange, DataSource
from app.models.device import ApprovedSoftware, Device, DevicePolicyChange
from app.models.rbac import User
from app.models.risk_control import Control
from app.schemas.notification import PendingApprovalOut
from app.services.auth_service import get_user_permission_names

# Each entry: (permission needed to decide on it, the collector). Only the
# maker-checker flows that have an explicit "someone requested this, a
# different person must decide" shape are included here — raw mapping
# suggestions (auto/needs_review) have no requester and no bounded queue,
# so they stay on the mapping screen's own bold/orange highlighting instead
# of flooding this bell.
_PENDING_PERMISSION = {
    "rule": "audit_framework:manage",
    "control_activation": "audit_framework:manage",
    "control_deactivation": "audit_framework:manage",
    "data_connection_change": "data_sources:approve_change",
    "device_policy_change": "devices:approve_policy",
    "approved_software": "devices:manage_policy",
    "device_revocation": "devices:approve_revoke",
    "device_deletion": "devices:approve_delete",
}


def _pending_rules(db: Session, *, organization_id: uuid.UUID, exclude_user_id: uuid.UUID) -> list[PendingApprovalOut]:
    rows = db.execute(
        select(TestRule, AuditTest)
        .join(AuditTest, AuditTest.audit_test_id == TestRule.audit_test_id)
        .where(AuditTest.organization_id == organization_id, TestRule.status == "pending_approval", TestRule.created_by != exclude_user_id)
    )
    return [
        PendingApprovalOut(
            category="rule",
            entity_id=rule.rule_id,
            label=f"Rule “{rule.rule_name}” for {test.test_code or test.test_name}",
            detail="Awaiting approval before it can execute.",
            requested_at=rule.created_at,
            link_path="/audit-tests",
        )
        for rule, test in rows
    ]


def _pending_controls(db: Session, *, organization_id: uuid.UUID, exclude_user_id: uuid.UUID) -> list[PendingApprovalOut]:
    rows = db.scalars(
        select(Control).where(
            Control.organization_id == organization_id,
            Control.status.in_(("pending_activation", "pending_deactivation")),
        )
    )
    out = []
    for control in rows:
        if control.status == "pending_activation":
            if control.activation_requested_by == exclude_user_id:
                continue
            out.append(
                PendingApprovalOut(
                    category="control_activation",
                    entity_id=control.control_id,
                    label=f"Activate control {control.control_code} — {control.control_name}",
                    requested_at=control.updated_at,
                    link_path="/controls",
                )
            )
        else:
            if control.deactivation_requested_by == exclude_user_id:
                continue
            out.append(
                PendingApprovalOut(
                    category="control_deactivation",
                    entity_id=control.control_id,
                    label=f"Deactivate control {control.control_code} — {control.control_name}",
                    detail=control.deactivation_requested_reason,
                    requested_at=control.updated_at,
                    link_path="/controls",
                )
            )
    return out


def _pending_connection_changes(db: Session, *, organization_id: uuid.UUID, exclude_user_id: uuid.UUID) -> list[PendingApprovalOut]:
    rows = db.execute(
        select(DataConnectionChange, DataConnection)
        .join(DataConnection, DataConnection.connection_id == DataConnectionChange.connection_id)
        .join(DataSource, DataSource.data_source_id == DataConnection.data_source_id)
        .where(
            DataSource.organization_id == organization_id,
            DataConnectionChange.approval_status == "pending_approval",
            DataConnectionChange.requested_by != exclude_user_id,
        )
    )
    return [
        PendingApprovalOut(
            category="data_connection_change",
            entity_id=change.change_id,
            label=f"{change.change_type.capitalize()} connection “{connection.connection_name or connection.host}”",
            requested_at=change.requested_at,
            link_path="/data-sources",
        )
        for change, connection in rows
    ]


def _pending_device_policy_changes(db: Session, *, organization_id: uuid.UUID, exclude_user_id: uuid.UUID) -> list[PendingApprovalOut]:
    rows = db.scalars(
        select(DevicePolicyChange).where(
            DevicePolicyChange.organization_id == organization_id,
            DevicePolicyChange.approval_status == "pending_approval",
            DevicePolicyChange.requested_by != exclude_user_id,
        )
    )
    return [
        PendingApprovalOut(
            category="device_policy_change",
            entity_id=row.policy_change_id,
            label="Device compliance policy change",
            requested_at=row.requested_at,
            link_path="/devices",
        )
        for row in rows
    ]


def _pending_approved_software(db: Session, *, organization_id: uuid.UUID, exclude_user_id: uuid.UUID) -> list[PendingApprovalOut]:
    rows = db.scalars(
        select(ApprovedSoftware).where(
            ApprovedSoftware.organization_id == organization_id,
            ApprovedSoftware.approval_status == "pending_approval",
            ApprovedSoftware.created_by != exclude_user_id,
        )
    )
    return [
        PendingApprovalOut(
            category="approved_software",
            entity_id=row.approved_software_id,
            label=f"Classify “{row.app_name}” as {row.classification}",
            requested_at=row.created_at,
            link_path="/devices",
        )
        for row in rows
    ]


def _pending_device_lifecycle(db: Session, *, organization_id: uuid.UUID, exclude_user_id: uuid.UUID) -> list[PendingApprovalOut]:
    rows = db.scalars(
        select(Device).where(
            Device.organization_id == organization_id,
            Device.status.in_(("pending_revocation", "pending_deletion")),
        )
    )
    out = []
    for device in rows:
        if device.status == "pending_revocation":
            if device.revocation_requested_by == exclude_user_id:
                continue
            out.append(
                PendingApprovalOut(
                    category="device_revocation",
                    entity_id=device.device_id,
                    label=f"Revoke device “{device.device_name}”",
                    detail=device.revocation_reason,
                    requested_at=device.revocation_requested_at or device.updated_at,
                    link_path="/devices",
                )
            )
        else:
            if device.deletion_requested_by == exclude_user_id:
                continue
            out.append(
                PendingApprovalOut(
                    category="device_deletion",
                    entity_id=device.device_id,
                    label=f"Delete device “{device.device_name}”",
                    detail=device.deletion_reason,
                    requested_at=device.deletion_requested_at or device.updated_at,
                    link_path="/devices",
                )
            )
    return out


def list_pending_approvals(db: Session, *, organization_id: uuid.UUID, user: User) -> list[PendingApprovalOut]:
    """Everything this specific user is both eligible to decide on (holds
    the permission the approve endpoint itself requires) and did not
    request themselves — mirrors each approve route's own authorization
    exactly, so the bell never advertises something clicking through to it
    would then 403 or self-approval-block on."""
    granted = get_user_permission_names(db, user.user_id)
    items: list[PendingApprovalOut] = []

    if _PENDING_PERMISSION["rule"] in granted:
        items += _pending_rules(db, organization_id=organization_id, exclude_user_id=user.user_id)
    if _PENDING_PERMISSION["control_activation"] in granted:
        items += _pending_controls(db, organization_id=organization_id, exclude_user_id=user.user_id)
    if _PENDING_PERMISSION["data_connection_change"] in granted:
        items += _pending_connection_changes(db, organization_id=organization_id, exclude_user_id=user.user_id)
    if _PENDING_PERMISSION["device_policy_change"] in granted:
        items += _pending_device_policy_changes(db, organization_id=organization_id, exclude_user_id=user.user_id)
    if _PENDING_PERMISSION["approved_software"] in granted:
        items += _pending_approved_software(db, organization_id=organization_id, exclude_user_id=user.user_id)
    if _PENDING_PERMISSION["device_revocation"] in granted or _PENDING_PERMISSION["device_deletion"] in granted:
        items += _pending_device_lifecycle(db, organization_id=organization_id, exclude_user_id=user.user_id)

    items.sort(key=lambda i: i.requested_at, reverse=True)
    return items
