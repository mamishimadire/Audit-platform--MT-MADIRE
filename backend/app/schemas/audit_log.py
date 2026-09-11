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
