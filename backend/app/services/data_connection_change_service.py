"""
Maker-checker for editing or disconnecting a live DataConnection — modeled
directly on device_policy_service.py's DevicePolicyChange flow. Renaming a
connection, changing its host/credentials, or taking it out of service all
change what the platform actually connects to and monitors, so (unlike the
plain is_hidden display toggles in data_source_service.py) these require a
different, independently-authorized user to approve before they take effect.

proposed_changes is stored keyed by DataConnection's own column names
(encrypted_password, not password) so a pending secret is never sitting in
this table in plaintext — request time encrypts it once, approval applies
it with a plain setattr loop, no second encryption step and no plaintext
round trip through JSON.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.crypto import encrypt_secret
from app.models.data_source import DataConnection, DataConnectionChange
from app.schemas.data_source import DataConnectionUpdateRequest
from app.services.audit_log_service import log_action

# Payload field -> live DataConnection column, only where the name differs.
_SECRET_FIELD_MAP = {
    "password": "encrypted_password",
    "snowflake_key_passphrase": "encrypted_snowflake_key_passphrase",
}


def _to_column_changes(payload: DataConnectionUpdateRequest) -> dict:
    changes: dict = {}
    for field, value in payload.model_dump(exclude_none=True).items():
        if field in _SECRET_FIELD_MAP:
            changes[_SECRET_FIELD_MAP[field]] = encrypt_secret(value)
        else:
            changes[field] = value
    return changes


def list_changes_for_connection(db: Session, *, connection_id: uuid.UUID) -> list[DataConnectionChange]:
    return list(
        db.scalars(
            select(DataConnectionChange)
            .where(DataConnectionChange.connection_id == connection_id)
            .order_by(DataConnectionChange.requested_at.desc())
        )
    )


def _request_change(
    db: Session, *, connection: DataConnection, change_type: str, proposed_changes: dict,
    requested_by_user_id: uuid.UUID, organization_id: uuid.UUID, action_label: str,
) -> DataConnectionChange:
    existing = db.scalar(
        select(DataConnectionChange).where(
            DataConnectionChange.connection_id == connection.connection_id,
            DataConnectionChange.approval_status == "pending_approval",
        )
    )
    if existing is not None:
        raise ValueError("A change is already awaiting approval for this connection.")

    change = DataConnectionChange(
        connection_id=connection.connection_id,
        change_type=change_type,
        proposed_changes=proposed_changes,
        requested_by=requested_by_user_id,
        approval_status="pending_approval",
    )
    db.add(change)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("A change is already awaiting approval for this connection.") from exc

    log_action(
        db,
        action=action_label,
        organization_id=organization_id,
        user_id=requested_by_user_id,
        entity_type="data_connection_changes",
        entity_id=change.change_id,
        new_value={"change_type": change_type, "connection_id": str(connection.connection_id)},
    )
    db.commit()
    db.refresh(change)
    return change


def request_connection_update(
    db: Session, *, connection: DataConnection, payload: DataConnectionUpdateRequest,
    requested_by_user_id: uuid.UUID, organization_id: uuid.UUID,
) -> DataConnectionChange:
    return _request_change(
        db,
        connection=connection,
        change_type="update",
        proposed_changes=_to_column_changes(payload),
        requested_by_user_id=requested_by_user_id,
        organization_id=organization_id,
        action_label="Data connection edit requested — pending independent approval",
    )


def request_connection_disconnect(
    db: Session, *, connection: DataConnection, requested_by_user_id: uuid.UUID, organization_id: uuid.UUID,
) -> DataConnectionChange:
    return _request_change(
        db,
        connection=connection,
        change_type="disconnect",
        proposed_changes={},
        requested_by_user_id=requested_by_user_id,
        organization_id=organization_id,
        action_label="Data connection disconnect requested — pending independent approval",
    )


def request_connection_delete(
    db: Session, *, connection: DataConnection, requested_by_user_id: uuid.UUID, organization_id: uuid.UUID,
) -> DataConnectionChange:
    return _request_change(
        db,
        connection=connection,
        change_type="delete",
        proposed_changes={},
        requested_by_user_id=requested_by_user_id,
        organization_id=organization_id,
        action_label="Data connection deletion requested — pending independent approval",
    )


def approve_connection_change(
    db: Session, *, change: DataConnectionChange, connection: DataConnection, approved_by_user_id: uuid.UUID, organization_id: uuid.UUID,
) -> DataConnectionChange:
    if change.approval_status != "pending_approval":
        raise ValueError(f"Cannot approve — this change is '{change.approval_status}', not pending approval.")
    if change.requested_by is not None and change.requested_by == approved_by_user_id:
        raise ValueError("You requested this change yourself — a different authorized user must approve it.")

    if change.change_type == "disconnect":
        # Reuses the status HubSpot's own token-refresh failure already sets
        # (see oauth_connection_service._mark_revoked) — 'revoked' already
        # means "out of service, not attempting to reconnect automatically"
        # for a DataConnection, so this isn't a new state, just a new way
        # to reach it. History (past test results, discovered entities) is
        # kept — this is a deactivation, never a delete.
        connection.connection_status = "revoked"
    elif change.change_type == "update":
        for column, value in change.proposed_changes.items():
            setattr(connection, column, value)
    # 'delete' needs no live-row change here — it's applied after the audit
    # log entry below, since that's the only durable record of what this
    # connection was once the row (and, via CASCADE, this very change row)
    # is actually gone.

    change.approval_status = "approved"
    change.approved_by = approved_by_user_id
    change.approved_at = datetime.now(timezone.utc)
    log_action(
        db,
        action=f"Approved data connection {change.change_type}",
        organization_id=organization_id,
        user_id=approved_by_user_id,
        entity_type="data_connection_changes",
        entity_id=change.change_id,
        new_value={
            "change_type": change.change_type,
            **(
                {
                    "connection_id": str(connection.connection_id),
                    "connection_name": connection.connection_name,
                    "db_type": connection.db_type,
                    "host": connection.host,
                    "database_name": connection.database_name,
                }
                if change.change_type == "delete"
                else {}
            ),
        },
    )
    if change.change_type == "delete":
        # Deleting the connection cascades to its own OAuthConnection and
        # data_connection_changes rows (this one included) — expunge first
        # so the in-memory object we're about to return stays readable
        # after commit instead of SQLAlchemy trying to re-SELECT a row that
        # no longer exists.
        db.flush()
        db.expunge(change)
        db.delete(connection)
    db.commit()
    if change.change_type != "delete":
        db.refresh(change)
    return change


def reject_connection_change(
    db: Session, *, change: DataConnectionChange, reason: str, rejected_by_user_id: uuid.UUID, organization_id: uuid.UUID,
) -> DataConnectionChange:
    """A different person from whoever requested this change — the
    requester withdraws their own request via cancel_connection_change
    instead, never this."""
    if change.approval_status != "pending_approval":
        raise ValueError(f"Cannot reject — this change is '{change.approval_status}', not pending approval.")
    if change.requested_by is not None and change.requested_by == rejected_by_user_id:
        raise ValueError("You requested this change yourself — a different authorized user must reject it, or cancel your own request instead.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reject a data connection change.")

    change.approval_status = "rejected"
    change.rejected_by = rejected_by_user_id
    change.rejected_at = datetime.now(timezone.utc)
    change.rejected_reason = reason.strip()
    log_action(
        db,
        action=f"Rejected data connection {change.change_type}: {change.rejected_reason}",
        organization_id=organization_id,
        user_id=rejected_by_user_id,
        entity_type="data_connection_changes",
        entity_id=change.change_id,
        new_value={"approval_status": "rejected", "reason": change.rejected_reason},
    )
    db.commit()
    db.refresh(change)
    return change


def cancel_connection_change(
    db: Session, *, change: DataConnectionChange, reason: str, cancelled_by_user_id: uuid.UUID, organization_id: uuid.UUID,
) -> DataConnectionChange:
    """The requester withdrawing their OWN still-pending data connection
    change request — only they may do this, no one else."""
    if change.approval_status != "pending_approval":
        raise ValueError(f"Cannot cancel — this change is '{change.approval_status}', not pending approval.")
    if change.requested_by != cancelled_by_user_id:
        raise ValueError("Only the person who requested this change can cancel it.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to cancel a data connection change request.")

    change.approval_status = "rejected"
    change.rejected_by = cancelled_by_user_id
    change.rejected_at = datetime.now(timezone.utc)
    change.rejected_reason = reason.strip()
    log_action(
        db,
        action=f"Cancelled own data connection {change.change_type} request: {change.rejected_reason}",
        organization_id=organization_id,
        user_id=cancelled_by_user_id,
        entity_type="data_connection_changes",
        entity_id=change.change_id,
        new_value={"approval_status": "rejected", "reason": change.rejected_reason},
    )
    db.commit()
    db.refresh(change)
    return change
