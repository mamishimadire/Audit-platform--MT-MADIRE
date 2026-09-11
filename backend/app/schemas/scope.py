import uuid
from datetime import datetime

from app.schemas.common import OrmModel


class ScopeGrant(OrmModel):
    user_id: uuid.UUID


class ScopeOut(OrmModel):
    scope_id: uuid.UUID
    user_id: uuid.UUID
    organization_id: uuid.UUID
    granted_by: uuid.UUID | None
    created_at: datetime
