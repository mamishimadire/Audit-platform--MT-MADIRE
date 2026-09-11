import uuid

from sqlalchemy.orm import Session

from app.core.request_context import get_client_ip
from app.models.audit_log import AuditLog


def log_action(
    db: Session,
    *,
    action: str,
    organization_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    old_value: dict | None = None,
    new_value: dict | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """
    Writes one audit_logs row. Callers commit as part of their own transaction
    so the log entry never survives a rolled-back business change (and never
    gets lost if the caller forgets to commit).

    ip_address defaults to the current request's client IP (set by
    capture_client_ip middleware) — explicitly pass a value only to override
    that (or pass "" if a caller must force it blank outside a request).
    """
    entry = AuditLog(
        organization_id=organization_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=old_value,
        new_value=new_value,
        ip_address=ip_address if ip_address is not None else get_client_ip(),
    )
    db.add(entry)
    return entry
