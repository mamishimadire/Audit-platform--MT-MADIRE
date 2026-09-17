import uuid
from datetime import datetime

from app.schemas.common import OrmModel


class PendingApprovalOut(OrmModel):
    """One item in the notification bell: something a maker requested that
    this specific user is both eligible to decide on and did not request
    themselves. category is a stable machine key (e.g. 'rule',
    'control_activation') the frontend uses to route the click; label is
    the human-readable line shown in the dropdown."""

    category: str
    entity_id: uuid.UUID
    label: str
    detail: str | None = None
    requested_at: datetime
    link_path: str


class NotificationDismissRequest(OrmModel):
    category: str
    entity_id: uuid.UUID
    label: str
    detail: str | None = None
    requested_at: datetime
    link_path: str
