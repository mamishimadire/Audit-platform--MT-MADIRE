import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, uuid_pk


class Evidence(Base):
    __tablename__ = "evidence"

    evidence_id: Mapped[uuid.UUID] = uuid_pk("evidence_id")
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_executions.execution_id", ondelete="CASCADE"), nullable=False
    )
    evidence_type: Mapped[str | None] = mapped_column(String(50))
    evidence_location: Mapped[str | None] = mapped_column(Text)
    evidence_hash: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Exception_(Base):
    """Maps to the `exceptions` table. Named Exception_ to avoid shadowing the Python builtin."""

    __tablename__ = "exceptions"

    exception_id: Mapped[uuid.UUID] = uuid_pk("exception_id")
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_executions.execution_id", ondelete="CASCADE"), nullable=False
    )
    exception_reference: Mapped[str | None] = mapped_column(String(50))
    exception_description: Mapped[str | None] = mapped_column(Text)
    recommended_remediation: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Re-detecting the same unresolved condition updates these instead of
    # inserting a new row — detected_at keeps meaning "first ever seen."
    last_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class ExceptionRecord(Base):
    __tablename__ = "exception_records"

    exception_record_id: Mapped[uuid.UUID] = uuid_pk("exception_record_id")
    exception_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exceptions.exception_id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_entities.entity_id", ondelete="SET NULL")
    )
    record_identifier: Mapped[str | None] = mapped_column(String(255))
    exception_data: Mapped[dict | None] = mapped_column(JSONB)
