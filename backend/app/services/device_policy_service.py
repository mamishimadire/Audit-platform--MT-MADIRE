"""
Layer 3 (Policy & Compliance Engine): what an organization actually requires
of its devices. Stored as a JSON blob in the existing organization_settings
table rather than a new table — it's a handful of booleans with no
independent lifecycle, so a dedicated table would be pure ceremony.

Device-group targeting (different policies for different device groups)
isn't built — this is one policy per organization, applied to every
enrolled device. That's an intentional v1 scope cut, not an oversight.
"""
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.device import DevicePolicyChange
from app.models.organization import OrganizationSetting
from app.services.audit_log_service import log_action

POLICY_SETTING_NAME = "device_compliance_policy"

DEFAULT_POLICY = {
    "require_antivirus": True,
    "require_firewall": True,
    "require_disk_encryption": True,
    "require_os_up_to_date": True,
    "require_software_compliance": True,
}


def get_device_policy(db: Session, *, organization_id: uuid.UUID) -> dict:
    setting = db.scalar(
        select(OrganizationSetting).where(
            OrganizationSetting.organization_id == organization_id, OrganizationSetting.setting_name == POLICY_SETTING_NAME
        )
    )
    if setting is None or not setting.setting_value:
        return dict(DEFAULT_POLICY)
    try:
        stored = json.loads(setting.setting_value)
    except (json.JSONDecodeError, TypeError):
        return dict(DEFAULT_POLICY)
    # Merge over the default so an older stored policy missing a newer key
    # (e.g. a check added after this org last saved its policy) still
    # behaves as "required" rather than silently becoming unenforced.
    return {**DEFAULT_POLICY, **stored}


def list_device_policy_changes(db: Session, *, organization_id: uuid.UUID) -> list[DevicePolicyChange]:
    """Newest first, so reviewers can see the pending request and recent
    decisions without confusing either with the effective policy."""
    return list(
        db.scalars(
            select(DevicePolicyChange)
            .where(DevicePolicyChange.organization_id == organization_id)
            .order_by(DevicePolicyChange.requested_at.desc())
        )
    )


def request_device_policy_change(
    db: Session, *, organization_id: uuid.UUID, policy: dict, requested_by_user_id: uuid.UUID
) -> DevicePolicyChange:
    """Store a proposal without changing the policy agents currently enforce."""
    existing = db.scalar(
        select(DevicePolicyChange).where(
            DevicePolicyChange.organization_id == organization_id,
            DevicePolicyChange.approval_status == "pending_approval",
        )
    )
    if existing is not None:
        raise ValueError("A device compliance policy change is already awaiting approval for this organization.")

    proposed = {**DEFAULT_POLICY, **policy}
    change = DevicePolicyChange(
        organization_id=organization_id,
        proposed_policy=proposed,
        requested_by=requested_by_user_id,
        approval_status="pending_approval",
    )
    db.add(change)
    try:
        db.flush()
    except IntegrityError as exc:
        # The partial unique index is the final guard if two editors submit
        # at precisely the same time.
        db.rollback()
        raise ValueError("A device compliance policy change is already awaiting approval for this organization.") from exc

    log_action(
        db,
        action="Device compliance policy change requested — pending independent approval",
        organization_id=organization_id,
        user_id=requested_by_user_id,
        entity_type="device_policy_changes",
        entity_id=change.policy_change_id,
        old_value=get_device_policy(db, organization_id=organization_id),
        new_value=proposed,
    )
    db.commit()
    db.refresh(change)
    return change


def approve_device_policy_change(
    db: Session, *, change: DevicePolicyChange, approved_by_user_id: uuid.UUID
) -> DevicePolicyChange:
    """Make an independently approved proposal live, then re-evaluate the
    latest known device state without inventing a new heartbeat."""
    if change.approval_status != "pending_approval":
        raise ValueError(f"Cannot approve — this policy change is '{change.approval_status}', not pending approval.")
    if change.requested_by is not None and change.requested_by == approved_by_user_id:
        raise ValueError("You requested this policy change yourself — a different authorized user must approve it.")

    change.approval_status = "approved"
    change.approved_by = approved_by_user_id
    change.approved_at = datetime.now(timezone.utc)
    log_action(
        db,
        action="Approved device compliance policy change",
        organization_id=change.organization_id,
        user_id=approved_by_user_id,
        entity_type="device_policy_changes",
        entity_id=change.policy_change_id,
        new_value={"approval_status": "approved", "proposed_policy": change.proposed_policy},
    )
    # This commits the approval together with the effective policy and still
    # resolves stale exceptions for checks the approved policy disables.
    set_device_policy(
        db,
        organization_id=change.organization_id,
        policy=change.proposed_policy,
        updated_by_user_id=approved_by_user_id,
    )

    from app.services.device_compliance_service import recheck_all_devices_from_latest_telemetry
    from app.services.software_compliance_service import recheck_all_devices_for_org

    recheck_all_devices_from_latest_telemetry(db, organization_id=change.organization_id)
    recheck_all_devices_for_org(db, organization_id=change.organization_id)
    db.refresh(change)
    return change


def reject_device_policy_change(
    db: Session, *, change: DevicePolicyChange, reason: str, rejected_by_user_id: uuid.UUID
) -> DevicePolicyChange:
    if change.approval_status != "pending_approval":
        raise ValueError(f"Cannot reject — this policy change is '{change.approval_status}', not pending approval.")
    if change.requested_by is not None and change.requested_by == rejected_by_user_id:
        raise ValueError("You requested this policy change yourself — a different authorized user must reject it.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reject a device compliance policy change.")

    change.approval_status = "rejected"
    change.rejected_by = rejected_by_user_id
    change.rejected_at = datetime.now(timezone.utc)
    change.rejected_reason = reason.strip()
    log_action(
        db,
        action=f"Rejected device compliance policy change: {change.rejected_reason}",
        organization_id=change.organization_id,
        user_id=rejected_by_user_id,
        entity_type="device_policy_changes",
        entity_id=change.policy_change_id,
        new_value={"approval_status": "rejected", "reason": change.rejected_reason},
    )
    db.commit()
    db.refresh(change)
    return change


def _resolve_exceptions_for_newly_disabled_checks(
    db: Session, *, organization_id: uuid.UUID, previous: dict, new: dict, resolved_by_user_id: uuid.UUID
) -> int:
    """Turning a check off must mean its existing exceptions stop counting
    immediately, not just stop creating new ones — otherwise a disabled
    check keeps inflating Open Exceptions indefinitely for a condition the
    organization has explicitly said it no longer wants enforced. Deferred
    imports avoid a circular import: device_compliance_service already
    imports get_device_policy from this module at module level."""
    from app.models.audit_test import AuditTest
    from app.models.evidence_exception import Exception_
    from app.models.monitoring import TestExecution
    from app.services.device_compliance_service import _CHECKS, _POLICY_KEY_BY_FIELD, COMPLIANCE_TEST_CODE
    from app.services.exception_service import OPEN_STATUSES
    from app.services.software_compliance_service import SOFTWARE_TEST_CODE

    newly_disabled_endpoint_descriptions = {
        description
        for field, description, _severity, _guidance in _CHECKS
        if previous.get(_POLICY_KEY_BY_FIELD[field], True) and not new.get(_POLICY_KEY_BY_FIELD[field], True)
    }
    software_disabled = previous.get("require_software_compliance", True) and not new.get("require_software_compliance", True)

    if not newly_disabled_endpoint_descriptions and not software_disabled:
        return 0

    test_codes = [code for code, enabled in ((COMPLIANCE_TEST_CODE, newly_disabled_endpoint_descriptions), (SOFTWARE_TEST_CODE, software_disabled)) if enabled]
    open_exceptions = db.scalars(
        select(Exception_)
        .join(TestExecution, TestExecution.execution_id == Exception_.execution_id)
        .join(AuditTest, AuditTest.audit_test_id == TestExecution.audit_test_id)
        .where(AuditTest.organization_id == organization_id, AuditTest.test_code.in_(test_codes), Exception_.status.in_(OPEN_STATUSES))
    ).all()

    resolved_count = 0
    for exc in open_exceptions:
        # Software-disabled resolves every open AS-004 exception outright.
        # For endpoint checks (all sharing EP-001), only resolve the ones
        # matching a check that was JUST disabled — a still-enabled check's
        # exception on the same test must never get swept up too.
        matches_a_disabled_endpoint_check = any(
            exc.exception_description and exc.exception_description.endswith(f": {description}")
            for description in newly_disabled_endpoint_descriptions
        )
        if software_disabled or matches_a_disabled_endpoint_check:
            old_status = exc.status
            exc.status = "resolved"
            resolved_count += 1
            log_action(
                db,
                action=f"Exception auto-resolved: compliance check disabled by organization policy ({exc.exception_description})",
                organization_id=organization_id,
                user_id=resolved_by_user_id,
                entity_type="exceptions",
                entity_id=exc.exception_id,
                old_value={"status": old_status},
                new_value={"status": "resolved"},
            )
    return resolved_count


def set_device_policy(db: Session, *, organization_id: uuid.UUID, policy: dict, updated_by_user_id: uuid.UUID) -> dict:
    previous = get_device_policy(db, organization_id=organization_id)
    merged = {**DEFAULT_POLICY, **policy}
    setting = db.scalar(
        select(OrganizationSetting).where(
            OrganizationSetting.organization_id == organization_id, OrganizationSetting.setting_name == POLICY_SETTING_NAME
        )
    )
    value = json.dumps(merged, sort_keys=True)
    if setting is None:
        setting = OrganizationSetting(organization_id=organization_id, setting_name=POLICY_SETTING_NAME, setting_value=value)
        db.add(setting)
    else:
        setting.setting_value = value

    log_action(
        db,
        action="Device compliance policy updated",
        organization_id=organization_id,
        user_id=updated_by_user_id,
        entity_type="organization_settings",
        new_value=merged,
    )
    _resolve_exceptions_for_newly_disabled_checks(
        db, organization_id=organization_id, previous=previous, new=merged, resolved_by_user_id=updated_by_user_id
    )
    db.commit()
    return merged
