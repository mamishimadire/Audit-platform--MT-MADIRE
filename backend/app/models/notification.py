import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, uuid_pk


class NotificationDismissal(Base):
    """A user clearing one item out of their own notification bell.
    Pending-approval items are computed live from real table state (a
    pending rule, an exception assigned to you, ...) rather than stored
    as notification rows themselves, so "clearing" one doesn't change
    the underlying work — it just records that this user has seen it,
    the same way clearing an email doesn't delete the thread. A row
    here hides the (category, entity_id) pair from that user's current
    inbox; it still shows up in their notification history, which is
    why the notification's own text is snapshotted here rather than
    re-read live — the underlying thing (e.g. a pending rule) may have
    since been approved and no longer match any "pending" query at
    all, but the history entry should still read sensibly."""

    __tablename__ = "notification_dismissals"

    dismissal_id: Mapped[uuid.UUID] = uuid_pk("dismissal_id")
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    link_path: Mapped[str] = mapped_column(String(255), nullable=False)
    # The notification's own requested_at at the moment it was dismissed —
    # if it reappears later with a newer requested_at (something new
    # happened), that's a different occurrence and shows in "current"
    # again rather than staying hidden forever.
    notification_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
