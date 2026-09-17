import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text, func
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
    """One row per time an exception was (re-)detected — a real_time
    schedule re-detecting the SAME unresolved issue every few minutes
    accumulates one of these on every run, not just the first. detected_at
    is what lets a reader ask for just the latest ("current") without the
    full pile-up; see exception_service.list_records_for_exception."""

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
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EvidenceRequest(Base):
    """A named, tracked ask from an auditor for one specific piece of
    evidence against an exception ("AD deprovisioning ticket for JSMITH"),
    with a due date and its own awaiting/received lifecycle — distinct
    from Evidence above (system-generated proof a test ran) and from
    Exception_.status (the exception's own open/in_progress/... state, which
    this doesn't touch). The client can attach more than one file to a
    single request (see EvidenceFile) — status is 'received' once at
    least one file exists, back to 'awaiting' if every file is deleted."""

    __tablename__ = "evidence_requests"

    request_id: Mapped[uuid.UUID] = uuid_pk("request_id")
    exception_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exceptions.exception_id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="awaiting")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EvidenceFile(Base):
    """One file the client attached to an EvidenceRequest. A request can
    have several of these — uploading doesn't replace an earlier file,
    it adds another, and any one of them can be deleted independently
    (e.g. to remove one uploaded by mistake) without touching the rest.
    file_data is stored inline in Postgres rather than on disk or in
    object storage — no object storage exists yet (see
    execution_service.record_execution_report's evidence_location
    comment), and these are individual documents (tickets, checklists,
    logs), not something that needs a CDN."""

    __tablename__ = "evidence_files"

    evidence_file_id: Mapped[uuid.UUID] = uuid_pk("evidence_file_id")
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_requests.request_id", ondelete="CASCADE"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100))
    file_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ExceptionComment(Base):
    """One message in the auditor/client discussion thread on an exception —
    plain chronological log, no editing or deleting (an audit trail of who
    said what, when, is the point)."""

    __tablename__ = "exception_comments"

    comment_id: Mapped[uuid.UUID] = uuid_pk("comment_id")
    exception_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exceptions.exception_id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
