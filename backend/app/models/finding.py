import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, uuid_pk


class Finding(Base):
    __tablename__ = "findings"

    finding_id: Mapped[uuid.UUID] = uuid_pk("finding_id")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    exception_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exceptions.exception_id", ondelete="CASCADE"), nullable=False
    )
    finding_title: Mapped[str] = mapped_column(String(255), nullable=False)
    finding_description: Mapped[str | None] = mapped_column(Text)
    risk_rating: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    identified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class FindingRootCause(Base):
    __tablename__ = "finding_root_causes"

    root_cause_id: Mapped[uuid.UUID] = uuid_pk("root_cause_id")
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.finding_id", ondelete="CASCADE"), nullable=False
    )
    root_cause_category: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)


class RemediationAction(Base, TimestampMixin):
    __tablename__ = "remediation_actions"

    remediation_id: Mapped[uuid.UUID] = uuid_pk("remediation_id")
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.finding_id", ondelete="CASCADE"), nullable=False
    )
    action_description: Mapped[str] = mapped_column(Text, nullable=False)
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    target_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Who actually marked this complete — distinct from responsible_user_id
    # (an assignment, not necessarily who clicked the button) and needed so
    # create_retest can enforce performer != verifier when SoD applies.
    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )


class Retest(Base):
    __tablename__ = "retests"

    retest_id: Mapped[uuid.UUID] = uuid_pk("retest_id")
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.finding_id", ondelete="CASCADE"), nullable=False
    )
    audit_test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_tests.audit_test_id", ondelete="CASCADE"), nullable=False
    )
    retest_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    result: Mapped[str | None] = mapped_column(String(20))
    performed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    comments: Mapped[str | None] = mapped_column(Text)
