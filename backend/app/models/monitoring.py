import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, uuid_pk


class MonitoringSchedule(Base, TimestampMixin):
    """A monitoring cadence for one audit test. Same maker-checker shape as
    TestRule (see test_rule_service.py): a new schedule starts life
    'pending_approval' and is never picked up by resolve_due_tests_for_gateway
    until a DIFFERENT authorized user approves it — setting how often (or
    whether) a control gets tested is a live change to what's actually
    monitored, not something one person should be able to switch on alone.
    Changing the cadence on a test that already has an active schedule
    creates a new pending version (supersedes_schedule_id points back at the
    old one, which flips to 'superseded' on approval) instead of mutating
    the live row in place — mirrors update_test_rule's edit-an-active-row
    shape exactly."""

    __tablename__ = "monitoring_schedules"

    schedule_id: Mapped[uuid.UUID] = uuid_pk("schedule_id")
    audit_test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_tests.audit_test_id", ondelete="CASCADE"), nullable=False
    )
    frequency: Mapped[str] = mapped_column(String(30), nullable=False)
    next_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending_approval")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    supersedes_schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("monitoring_schedules.schedule_id", ondelete="SET NULL")
    )


class TestExecution(Base):
    __tablename__ = "test_executions"

    execution_id: Mapped[uuid.UUID] = uuid_pk("execution_id")
    audit_test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_tests.audit_test_id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="running")
    records_analyzed: Mapped[int | None] = mapped_column(Integer)
    exceptions_found: Mapped[int | None] = mapped_column(Integer)
    execution_log: Mapped[str | None] = mapped_column(Text)
    # Which exact rule VERSION produced this run — a rule row is immutable
    # once active (see TestRule.version/supersedes_rule_id), so this alone
    # is enough to reconstruct exactly what logic executed, without a
    # separate execution-to-mapping join table.
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_rules.rule_id", ondelete="SET NULL")
    )
