import uuid
from datetime import datetime

from app.schemas.common import OrmModel


class AuditLogOut(OrmModel):
    log_id: uuid.UUID
    organization_id: uuid.UUID | None
    user_id: uuid.UUID | None
    action: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    old_value: dict | None
    new_value: dict | None
    timestamp: datetime
    # Resolved server-side (see routes/audit_logs.py) rather than making
    # the frontend fetch users to match against user_id — GET /platform/
    # users requires organizations:manage, which no client-side role
    # holds, so a client viewing their own org's trail could never have
    # resolved an internal auditor's name that way anyway. None only when
    # user_id itself is null (a system-generated entry with no actor).
    user_name: str | None = None
    # Plain-English rendering of old_value/new_value (see routes/
    # audit_logs.py._change_summary) — None when there's genuinely
    # nothing to show (both are empty), which the frontend then just
    # omits rather than showing a blank/empty change cell.
    change_summary: str | None = None
