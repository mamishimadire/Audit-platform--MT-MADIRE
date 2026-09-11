import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, uuid_pk


class BusinessProcess(Base, TimestampMixin):
    __tablename__ = "business_processes"

    process_id: Mapped[uuid.UUID] = uuid_pk("process_id")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    process_name: Mapped[str] = mapped_column(String(255), nullable=False)
    process_code: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    process_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )


class ProcessActivity(Base, TimestampMixin):
    __tablename__ = "process_activities"

    activity_id: Mapped[uuid.UUID] = uuid_pk("activity_id")
    process_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_processes.process_id", ondelete="CASCADE"), nullable=False
    )
    activity_name: Mapped[str] = mapped_column(String(255), nullable=False)
    activity_description: Mapped[str | None] = mapped_column(Text)
    activity_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
