"""
Layer 2 (Endpoint Agent) "Approved Management Actions" — a fixed allowlist
of remote actions, never arbitrary execution. The device only ever pulls
(polls for pending commands on its own schedule); the platform never opens
an inbound connection to it, same principle as the Gateway.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.device import Device, DeviceCommand
from app.services.audit_log_service import log_action

# Every command type this system will ever execute on a device — anything
# not in this set is rejected before it can be queued, let alone run.
ALLOWED_COMMAND_TYPES = ("run_check_now", "restart")

_COMMAND_LABELS = {
    "run_check_now": "Run security & inventory check now",
    "restart": "Restart device",
}


def queue_command(
    db: Session, *, device: Device, command_type: str, requested_by_user_id: uuid.UUID
) -> DeviceCommand:
    if command_type not in ALLOWED_COMMAND_TYPES:
        raise ValueError(f"Unknown command type: {command_type}")

    command = DeviceCommand(device_id=device.device_id, command_type=command_type, requested_by=requested_by_user_id)
    db.add(command)
    db.flush()
    log_action(
        db,
        action=f"Queued device command: {_COMMAND_LABELS.get(command_type, command_type)}",
        organization_id=device.organization_id,
        user_id=requested_by_user_id,
        entity_type="devices",
        entity_id=device.device_id,
        new_value={"command_type": command_type},
    )
    db.commit()
    db.refresh(command)
    return command


def list_commands_for_device(db: Session, *, device_id: uuid.UUID) -> list[DeviceCommand]:
    return list(
        db.scalars(select(DeviceCommand).where(DeviceCommand.device_id == device_id).order_by(DeviceCommand.requested_at.desc()))
    )


def list_pending_commands(db: Session, *, device_id: uuid.UUID) -> list[DeviceCommand]:
    return list(
        db.scalars(select(DeviceCommand).where(DeviceCommand.device_id == device_id, DeviceCommand.status == "pending"))
    )


def mark_command_sent(db: Session, *, commands: list[DeviceCommand]) -> None:
    for command in commands:
        command.status = "sent"
    db.commit()


def report_command_result(
    db: Session, *, command: DeviceCommand, success: bool, message: str | None
) -> DeviceCommand:
    command.status = "completed" if success else "failed"
    command.completed_at = datetime.now(timezone.utc)
    command.result_message = message
    db.commit()
    db.refresh(command)
    return command
