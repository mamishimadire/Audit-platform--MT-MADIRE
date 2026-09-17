import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_test import AuditTest, TestDataMapping, TestRule
from app.models.data_source import DataConnection, DataConnectionChange, DataSource
from app.models.device import ApprovedSoftware, Device, DevicePolicyChange
from app.models.evidence_exception import EvidenceRequest, Exception_
from app.models.monitoring import MonitoringSchedule, TestExecution
from app.models.rbac import User
from app.models.risk_control import Control
from app.schemas.notification import PendingApprovalOut
from app.services.auth_service import get_user_permission_names
from app.services.exception_service import OPEN_STATUSES

# Every mapping row that exists was explicitly created by a human — either
# accepting a suggestion or mapping a field by hand (see mapping_service.
# create_mapping) — and, same as every other maker-checker flow here, a
# different person has to approve it before the control's test can rely on
# it. 'rejected'/'approved'/'superseded' are already-decided, not pending.
_PENDING_MAPPING_STATUSES = ("auto", "needs_review", "manually_mapped")

# Each entry: permission needed to decide on it -> the collector it gates.
_PENDING_PERMISSION = {
    "rule": "audit_framework:manage",
    "control_activation": "audit_framework:manage",
    "control_deactivation": "audit_framework:manage",
    "mapping": "audit_framework:manage",
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
            label=f"New rule “{rule.rule_name}” needs your OK",
            detail=f"For {test.test_code or test.test_name}. It will not start checking anything until someone says yes.",
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
                    label=f"Turn ON control {control.control_code} — {control.control_name}?",
                    detail="Someone wants to switch this control on. Say yes or no.",
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
                    label=f"Turn OFF control {control.control_code} — {control.control_name}?",
                    detail=(
                        f"Someone wants to switch this control off. They said: {control.deactivation_requested_reason}"
                        if control.deactivation_requested_reason
                        else "Someone wants to switch this control off."
                    ),
                    requested_at=control.updated_at,
                    link_path="/controls",
                )
            )
    return out


def _pending_mappings(db: Session, *, organization_id: uuid.UUID, exclude_user_id: uuid.UUID) -> list[PendingApprovalOut]:
    """Grouped per audit test (one notification line per control, not one
    per field) — a control can easily have a dozen mapped fields awaiting
    approval at once, and a dozen separate bell entries for the same
    control would be clutter, not information."""
    rows = db.execute(
        select(TestDataMapping, AuditTest)
        .join(AuditTest, AuditTest.audit_test_id == TestDataMapping.audit_test_id)
        .where(
            AuditTest.organization_id == organization_id,
            TestDataMapping.mapping_status.in_(_PENDING_MAPPING_STATUSES),
            TestDataMapping.created_by != exclude_user_id,
        )
    )
    grouped: dict[uuid.UUID, dict] = {}
    for mapping, test in rows:
        bucket = grouped.setdefault(test.audit_test_id, {"test": test, "count": 0, "latest": mapping.created_at})
        bucket["count"] += 1
        if mapping.created_at > bucket["latest"]:
            bucket["latest"] = mapping.created_at
    return [
        PendingApprovalOut(
            category="mapping",
            entity_id=test_id,
            label=f"{bucket['count']} thing{'s' if bucket['count'] != 1 else ''} to check for {bucket['test'].test_code or bucket['test'].test_name}",
            detail="These tell the system where to find the right information. Please look and say if they are correct.",
            requested_at=bucket["latest"],
            link_path="/audit-tests",
        )
        for test_id, bucket in grouped.items()
    ]


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
    _CHANGE_QUESTION = {
        "disconnect": "Stop using the connection",
        "delete": "Delete the connection",
        "update": "Change the details of the connection",
    }
    return [
        PendingApprovalOut(
            category="data_connection_change",
            entity_id=change.change_id,
            label=f"{_CHANGE_QUESTION.get(change.change_type, 'Change the connection')} “{connection.connection_name or connection.host}”?",
            detail="Say yes or no.",
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
            label="New rules for keeping devices safe",
            detail="Someone wants to change what counts as a safe device. Please check and say if it's OK.",
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
            label=f"Is the app “{row.app_name}” OK to use?",
            detail=f"Someone marked it as: {row.classification}. Please check and say if that's right.",
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
                    label=f"Turn off device “{device.device_name}”?",
                    detail=(
                        f"Someone wants to stop this device from being trusted. They said: {device.revocation_reason}"
                        if device.revocation_reason
                        else "Someone wants to stop this device from being trusted."
                    ),
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
                    label=f"Delete device “{device.device_name}”?",
                    detail=(
                        f"Someone wants to remove this device for good. They said: {device.deletion_reason}"
                        if device.deletion_reason
                        else "Someone wants to remove this device for good."
                    ),
                    requested_at=device.deletion_requested_at or device.updated_at,
                    link_path="/devices",
                )
            )
    return out


def _pending_schedules(db: Session, *, organization_id: uuid.UUID, exclude_user_id: uuid.UUID) -> list[PendingApprovalOut]:
    """Was missing entirely — added when monitoring schedules got their own
    maker-checker (migration 0062), but never wired into the bell, so a
    pending schedule change sat invisible until someone happened to open
    the audit test that requested it."""
    rows = db.execute(
        select(MonitoringSchedule, AuditTest)
        .join(AuditTest, AuditTest.audit_test_id == MonitoringSchedule.audit_test_id)
        .where(
            AuditTest.organization_id == organization_id,
            MonitoringSchedule.status == "pending_approval",
            MonitoringSchedule.created_by != exclude_user_id,
        )
    )
    return [
        PendingApprovalOut(
            category="monitoring_schedule",
            entity_id=schedule.schedule_id,
            label=f"New monitoring schedule for {test.test_code or test.test_name}",
            detail=f"Requested cadence: {schedule.frequency}. It will not run until someone approves it.",
            requested_at=schedule.created_at,
            link_path="/audit-tests",
        )
        for schedule, test in rows
    ]


def _your_open_exceptions(db: Session, *, organization_id: uuid.UUID, user_id: uuid.UUID) -> list[PendingApprovalOut]:
    """Not a maker-checker approval — every user sees their OWN assigned
    exceptions here, regardless of permission, same as the Exceptions page
    itself already shows them to their owner. This is what makes the bell
    a real inbox and not just an approvals queue: an Exception Owner has
    nothing to approve, but plenty that's genuinely waiting on them."""
    rows = db.execute(
        select(Exception_, TestExecution, AuditTest)
        .join(TestExecution, TestExecution.execution_id == Exception_.execution_id)
        .join(AuditTest, AuditTest.audit_test_id == TestExecution.audit_test_id)
        .where(
            AuditTest.organization_id == organization_id,
            Exception_.owner_id == user_id,
            Exception_.status.in_(OPEN_STATUSES),
        )
    )
    return [
        PendingApprovalOut(
            category="your_exception",
            entity_id=exception.exception_id,
            label=f"Exception assigned to you: {exception.exception_description or test.test_name}",
            detail=f"Status: {exception.status}. Resolve it (or update its status) once you've looked into it.",
            requested_at=exception.last_detected_at,
            link_path="/exceptions",
        )
        for exception, _execution, test in rows
    ]


def _evidence_requests_waiting_on_you(db: Session, *, organization_id: uuid.UUID, exclude_user_id: uuid.UUID) -> list[PendingApprovalOut]:
    """Any org member can upload against a request (see routes/exceptions.
    py's evidence-requests comment), so this is shown to everyone in the
    organization except whoever asked for it — they're the one waiting,
    not the one being waited on."""
    rows = db.execute(
        select(EvidenceRequest, Exception_)
        .join(Exception_, Exception_.exception_id == EvidenceRequest.exception_id)
        .join(TestExecution, TestExecution.execution_id == Exception_.execution_id)
        .join(AuditTest, AuditTest.audit_test_id == TestExecution.audit_test_id)
        .where(
            AuditTest.organization_id == organization_id,
            EvidenceRequest.status == "awaiting",
            EvidenceRequest.requested_by != exclude_user_id,
        )
    )
    return [
        PendingApprovalOut(
            category="evidence_request",
            entity_id=request.request_id,
            label=f"Evidence needed: {request.description}",
            detail=f"Due {request.due_date}" if request.due_date else "No due date set.",
            requested_at=request.requested_at,
            link_path="/exceptions",
        )
        for request, _exception in rows
    ]


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
    if _PENDING_PERMISSION["mapping"] in granted:
        items += _pending_mappings(db, organization_id=organization_id, exclude_user_id=user.user_id)
    if _PENDING_PERMISSION["data_connection_change"] in granted:
        items += _pending_connection_changes(db, organization_id=organization_id, exclude_user_id=user.user_id)
    if _PENDING_PERMISSION["device_policy_change"] in granted:
        items += _pending_device_policy_changes(db, organization_id=organization_id, exclude_user_id=user.user_id)
    if _PENDING_PERMISSION["approved_software"] in granted:
        items += _pending_approved_software(db, organization_id=organization_id, exclude_user_id=user.user_id)
    if _PENDING_PERMISSION["device_revocation"] in granted or _PENDING_PERMISSION["device_deletion"] in granted:
        items += _pending_device_lifecycle(db, organization_id=organization_id, exclude_user_id=user.user_id)
    if "audit_framework:manage" in granted:
        items += _pending_schedules(db, organization_id=organization_id, exclude_user_id=user.user_id)

    # Not gated by permission — every user sees their own inbox items
    # regardless of what they're eligible to approve.
    items += _your_open_exceptions(db, organization_id=organization_id, user_id=user.user_id)
    items += _evidence_requests_waiting_on_you(db, organization_id=organization_id, exclude_user_id=user.user_id)

    items.sort(key=lambda i: i.requested_at, reverse=True)
    return items
