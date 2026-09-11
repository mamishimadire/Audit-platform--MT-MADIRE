import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import fingerprint, generate_gateway_api_key, generate_registration_code
from app.models.device import Device, DeviceTelemetry
from app.models.evidence_exception import Exception_, ExceptionRecord
from app.schemas.device import ComplianceStatus, DeviceCreate
from app.services.audit_log_service import log_action
from app.services.exception_service import OPEN_STATUSES

REGISTRATION_CODE_TTL_MINUTES = 15

# Mirrors device_compliance_service._CHECKS — a device is only "compliant"
# once every check has actually reported a value and every one is True. An
# unreported/unknown check keeps the device at "unknown", never a false
# "compliant".
_COMPLIANCE_FIELDS = ("antivirus_enabled", "firewall_enabled", "disk_encryption_enabled", "os_up_to_date")


def create_device(db: Session, *, organization_id: uuid.UUID, payload: DeviceCreate, created_by_user_id: uuid.UUID) -> Device:
    device = Device(
        organization_id=organization_id,
        device_name=payload.device_name,
        assigned_user_name=payload.assigned_user_name,
        registration_code=generate_registration_code(),
        registration_code_expires_at=datetime.now(timezone.utc) + timedelta(minutes=REGISTRATION_CODE_TTL_MINUTES),
        registration_status="unused",
        status="pending",
        created_by=created_by_user_id,
    )
    db.add(device)
    db.flush()
    log_action(
        db,
        action=f"Generated device registration code for '{device.device_name}'",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="devices",
        entity_id=device.device_id,
        new_value={"device_name": device.device_name},
    )
    db.commit()
    db.refresh(device)
    return device


def list_devices(db: Session, *, organization_id: uuid.UUID) -> list[Device]:
    return list(
        db.scalars(select(Device).where(Device.organization_id == organization_id, Device.status != "deleted"))
    )


def list_deleted_devices(db: Session, *, organization_id: uuid.UUID) -> list[Device]:
    """The history the live fleet view deliberately hides — every device
    this organization has ever decommissioned, with who asked, why, and who
    approved it, since deletion is a soft delete (see approve_deletion)."""
    return list(
        db.scalars(
            select(Device)
            .where(Device.organization_id == organization_id, Device.status == "deleted")
            .order_by(Device.deletion_approved_at.desc())
        )
    )


def get_latest_telemetry(db: Session, *, device_id: uuid.UUID) -> DeviceTelemetry | None:
    return db.scalar(
        select(DeviceTelemetry).where(DeviceTelemetry.device_id == device_id).order_by(DeviceTelemetry.collected_at.desc()).limit(1)
    )


_POLICY_KEY_BY_FIELD = {
    "antivirus_enabled": "require_antivirus",
    "firewall_enabled": "require_firewall",
    "disk_encryption_enabled": "require_disk_encryption",
    "os_up_to_date": "require_os_up_to_date",
}


def compliance_status_from_telemetry(telemetry: DeviceTelemetry | None, policy: dict | None = None) -> ComplianceStatus:
    if telemetry is None:
        return "unknown"
    fields = [f for f in _COMPLIANCE_FIELDS if policy is None or policy.get(_POLICY_KEY_BY_FIELD[f], True)]
    if not fields:
        return "unknown"
    values = [getattr(telemetry, field) for field in fields]
    if any(v is None for v in values):
        return "unknown"
    return "compliant" if all(values) else "non_compliant"


def redeem_registration_code(
    db: Session, *, registration_code: str, device_name: str | None, hostname: str | None, os_name: str | None,
    os_version: str | None, agent_version: str | None,
) -> tuple[Device, str]:
    device = db.scalar(select(Device).where(Device.registration_code == registration_code))
    if device is None:
        raise ValueError("Invalid registration code")
    if device.registration_status != "unused":
        raise ValueError("Registration code has already been used or revoked")
    if device.registration_code_expires_at is None or device.registration_code_expires_at < datetime.now(timezone.utc):
        raise ValueError("Registration code has expired")

    api_key = generate_gateway_api_key()
    device.device_certificate_fingerprint = fingerprint(api_key)
    device.registration_status = "registered"
    device.status = "pending"
    device.registration_code = None
    device.registration_code_expires_at = None
    if device_name:
        device.device_name = device_name
    if hostname:
        device.hostname = hostname
    if os_name:
        device.os_name = os_name
    if os_version:
        device.os_version = os_version
    if agent_version:
        device.agent_version = agent_version

    log_action(
        db,
        action=f"Device '{device.device_name}' registered",
        organization_id=device.organization_id,
        entity_type="devices",
        entity_id=device.device_id,
        new_value={"registration_status": "registered", "hostname": hostname},
    )
    db.commit()
    db.refresh(device)
    return device, api_key


def authenticate_device(db: Session, *, device_id: uuid.UUID, api_key: str) -> Device | None:
    device = db.get(Device, device_id)
    if device is None or device.device_certificate_fingerprint is None:
        return None
    if device.registration_status != "registered" or device.status in ("deregistered", "deleted"):
        return None
    if device.device_certificate_fingerprint != fingerprint(api_key):
        return None
    return device


_REVOCABLE_STATUSES = ("pending", "online", "offline")


def request_revocation(db: Session, *, device: Device, reason: str, requested_by_user_id: uuid.UUID) -> Device:
    """Taking a device out of monitoring scope is no longer a single click —
    same dual-control principle already applied to control deactivation:
    the person who wants a device stopped is never the one who gets to
    make that happen unilaterally."""
    if device.status not in _REVOCABLE_STATUSES:
        raise ValueError(f"Cannot request revocation — device is '{device.status}'.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to request revocation.")

    device.status = "pending_revocation"
    device.revocation_requested_by = requested_by_user_id
    device.revocation_requested_at = datetime.now(timezone.utc)
    device.revocation_reason = reason
    device.revocation_approved_by = None
    device.revocation_approved_at = None
    log_action(
        db,
        action=f"Requested revocation of device '{device.device_name}': {reason}",
        organization_id=device.organization_id,
        user_id=requested_by_user_id,
        entity_type="devices",
        entity_id=device.device_id,
        new_value={"status": "pending_revocation", "reason": reason},
    )
    db.commit()
    db.refresh(device)
    return device


def approve_revocation(db: Session, *, device: Device, approved_by_user_id: uuid.UUID) -> Device:
    if device.status != "pending_revocation":
        raise ValueError(f"Cannot approve — device is '{device.status}', not pending revocation.")
    if device.revocation_requested_by is not None and device.revocation_requested_by == approved_by_user_id:
        raise ValueError("You requested this revocation yourself — a different authorized user must approve it.")

    device.status = "deregistered"
    device.registration_status = "revoked"
    device.revocation_approved_by = approved_by_user_id
    device.revocation_approved_at = datetime.now(timezone.utc)
    log_action(
        db,
        action=f"Approved revocation of device '{device.device_name}'",
        organization_id=device.organization_id,
        user_id=approved_by_user_id,
        entity_type="devices",
        entity_id=device.device_id,
        new_value={"status": "deregistered"},
    )
    db.commit()
    db.refresh(device)
    return device


def reject_revocation(db: Session, *, device: Device, reason: str, rejected_by_user_id: uuid.UUID) -> Device:
    if device.status != "pending_revocation":
        raise ValueError(f"Cannot reject — device is '{device.status}', not pending revocation.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reject a revocation request.")

    # Falls back to 'offline' rather than trying to remember the exact prior
    # status — the next heartbeat (if the device is still reporting) flips
    # it straight back to 'online' anyway, same as any other device.
    device.status = "offline"
    device.revocation_requested_by = None
    device.revocation_reason = None
    log_action(
        db,
        action=f"Rejected revocation of device '{device.device_name}': {reason}",
        organization_id=device.organization_id,
        user_id=rejected_by_user_id,
        entity_type="devices",
        entity_id=device.device_id,
        new_value={"status": "offline", "reason": reason},
    )
    db.commit()
    db.refresh(device)
    return device


def regenerate_registration_code(db: Session, *, device: Device, requested_by_user_id: uuid.UUID) -> Device:
    """Re-enrolling a revoked (or even still-active) device reuses the same
    device row — its history (telemetry, exceptions raised against it)
    stays attached to one identity instead of forking into a duplicate."""
    device.registration_code = generate_registration_code()
    device.registration_code_expires_at = datetime.now(timezone.utc) + timedelta(minutes=REGISTRATION_CODE_TTL_MINUTES)
    device.registration_status = "unused"
    device.status = "pending"
    device.device_certificate_fingerprint = None
    log_action(
        db,
        action=f"Generated new registration code for device '{device.device_name}'",
        organization_id=device.organization_id,
        user_id=requested_by_user_id,
        entity_type="devices",
        entity_id=device.device_id,
    )
    db.commit()
    db.refresh(device)
    return device


def _auto_close_exceptions_for_deleted_device(db: Session, *, device: Device, closed_by_user_id: uuid.UUID) -> int:
    """A deleted device can never be remediated — leaving its exceptions
    sitting at status 'open' forever would keep them counted as outstanding
    work on the dashboard and cluttering the Exceptions list for a problem
    nobody can act on anymore. The exception ROW itself is kept (see
    approve_deletion's docstring — audit history stays); only its status
    changes, exactly like a person closing it, so the audit trail still
    shows what happened and why."""
    exception_ids = db.scalars(
        select(ExceptionRecord.exception_id).where(ExceptionRecord.exception_data["device_id"].astext == str(device.device_id))
    ).all()
    if not exception_ids:
        return 0
    open_exceptions = db.scalars(
        select(Exception_).where(Exception_.exception_id.in_(exception_ids), Exception_.status.in_(OPEN_STATUSES))
    ).all()
    for exc in open_exceptions:
        old_status = exc.status
        exc.status = "closed"
        log_action(
            db,
            action=f"Exception auto-closed: device '{device.device_name}' was deleted",
            organization_id=device.organization_id,
            user_id=closed_by_user_id,
            entity_type="exceptions",
            entity_id=exc.exception_id,
            old_value={"status": old_status},
            new_value={"status": "closed"},
        )
    return len(open_exceptions)


_DELETABLE_STATUSES = ("pending", "online", "offline", "deregistered")


def request_deletion(db: Session, *, device: Device, reason: str, requested_by_user_id: uuid.UUID) -> Device:
    if device.status not in _DELETABLE_STATUSES:
        raise ValueError(f"Cannot request deletion — device is '{device.status}'.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to request deletion.")

    device.status = "pending_deletion"
    device.deletion_requested_by = requested_by_user_id
    device.deletion_requested_at = datetime.now(timezone.utc)
    device.deletion_reason = reason
    device.deletion_approved_by = None
    device.deletion_approved_at = None
    log_action(
        db,
        action=f"Requested deletion of device '{device.device_name}': {reason}",
        organization_id=device.organization_id,
        user_id=requested_by_user_id,
        entity_type="devices",
        entity_id=device.device_id,
        new_value={"status": "pending_deletion", "reason": reason},
    )
    db.commit()
    db.refresh(device)
    return device


def approve_deletion(db: Session, *, device: Device, approved_by_user_id: uuid.UUID) -> Device:
    """
    Soft delete — the device row stays (with who requested/approved its
    deletion and why) so "which devices were deleted, when, and why" stays
    a normal, queryable answer instead of just prose in the audit log; see
    list_deleted_devices. Its telemetry/inventory/pending commands are NOT
    purged — the point of keeping the row is to keep its history readable,
    which a cascade-delete of everything it ever reported would defeat.
    Any of its still-open exceptions are auto-closed: a deleted device can
    never be remediated, so leaving them 'open' forever would keep inflating
    dashboard counts for a problem nobody can act on anymore.
    """
    if device.status != "pending_deletion":
        raise ValueError(f"Cannot approve — device is '{device.status}', not pending deletion.")
    if device.deletion_requested_by is not None and device.deletion_requested_by == approved_by_user_id:
        raise ValueError("You requested this deletion yourself — a different authorized user must approve it.")

    closed_count = _auto_close_exceptions_for_deleted_device(db, device=device, closed_by_user_id=approved_by_user_id)
    device.status = "deleted"
    device.deletion_approved_by = approved_by_user_id
    device.deletion_approved_at = datetime.now(timezone.utc)
    log_action(
        db,
        action=f"Approved deletion of device '{device.device_name}'" + (f" ({closed_count} open exception(s) auto-closed)" if closed_count else ""),
        organization_id=device.organization_id,
        user_id=approved_by_user_id,
        entity_type="devices",
        entity_id=device.device_id,
        new_value={"status": "deleted"},
    )
    db.commit()
    db.refresh(device)
    return device


def reject_deletion(db: Session, *, device: Device, reason: str, rejected_by_user_id: uuid.UUID) -> Device:
    if device.status != "pending_deletion":
        raise ValueError(f"Cannot reject — device is '{device.status}', not pending deletion.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reject a deletion request.")

    device.status = "offline"
    device.deletion_requested_by = None
    device.deletion_reason = None
    log_action(
        db,
        action=f"Rejected deletion of device '{device.device_name}': {reason}",
        organization_id=device.organization_id,
        user_id=rejected_by_user_id,
        entity_type="devices",
        entity_id=device.device_id,
        new_value={"status": "offline", "reason": reason},
    )
    db.commit()
    db.refresh(device)
    return device
