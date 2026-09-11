import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, uuid_pk


class AuditTest(Base, TimestampMixin):
    __tablename__ = "audit_tests"

    audit_test_id: Mapped[uuid.UUID] = uuid_pk("audit_test_id")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    test_code: Mapped[str | None] = mapped_column(String(50))
    test_name: Mapped[str] = mapped_column(String(255), nullable=False)
    test_description: Mapped[str | None] = mapped_column(Text)
    test_type: Mapped[str | None] = mapped_column(String(50))
    frequency: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )


class ControlAuditTest(Base):
    __tablename__ = "control_audit_tests"
    __table_args__ = (UniqueConstraint("control_id", "audit_test_id"),)

    control_test_id: Mapped[uuid.UUID] = uuid_pk("control_test_id")
    control_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("controls.control_id", ondelete="CASCADE"), nullable=False
    )
    audit_test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_tests.audit_test_id", ondelete="CASCADE"), nullable=False
    )


class TestDataMapping(Base, TimestampMixin):
    __tablename__ = "test_data_mappings"

    mapping_id: Mapped[uuid.UUID] = uuid_pk("mapping_id")
    audit_test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_tests.audit_test_id", ondelete="CASCADE"), nullable=False
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.data_source_id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    field_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_fields.field_id", ondelete="SET NULL")
    )
    canonical_field: Mapped[str | None] = mapped_column(String(150))
    confidence_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    mapping_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="needs_review")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Maker-checker: whoever creates a mapping must not be the one who
    # approves it — see mapping_service.approve_mapping.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    # Immutability: once approved, editing a mapping creates a new row
    # (version + 1, supersedes_mapping_id -> this one, this one's status
    # flips to 'superseded') instead of overwriting it in place — see
    # mapping_service.update_mapping_field.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    supersedes_mapping_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_data_mappings.mapping_id", ondelete="SET NULL")
    )


class TestRule(Base, TimestampMixin):
    __tablename__ = "test_rules"

    rule_id: Mapped[uuid.UUID] = uuid_pk("rule_id")
    audit_test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_tests.audit_test_id", ondelete="CASCADE"), nullable=False
    )
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_type: Mapped[str | None] = mapped_column(String(50))
    rule_definition: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str | None] = mapped_column(String(20))
    # 'pending_approval' | 'active' | 'rejected' | 'deleted' — a rule must be
    # approved by someone other than its creator before it can execute (see
    # test_rule_service.submit_rule/approve_rule); deleted rules are kept
    # (with a reason), never hard-removed, so a reviewer can see a control's
    # test logic changed over time and why.
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending_approval")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    # 'manual' | 'auto_generated' | 'auto_generated_edited' — a reviewer must
    # be able to tell standard-library logic from something customized for
    # this client, and by whom.
    origin: Mapped[str] = mapped_column(String(30), nullable=False, server_default="manual")
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("control_rule_templates.template_id", ondelete="SET NULL")
    )
    edited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when a canonical field this rule reads gets remapped/unmapped
    # after the rule was created — the rule keeps running on its last-known
    # mapping rather than silently changing what it tests, but is flagged
    # so an auditor notices and reviews it.
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    deleted_reason: Mapped[str | None] = mapped_column(Text)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Maker-checker: whoever creates/submits a rule must not be the one who
    # approves it — see test_rule_service.approve_rule. Same pattern as
    # TestDataMapping.approved_by/approved_at above.
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    # Immutability: editing an active (approved) rule creates a new row
    # (version + 1, supersedes_rule_id -> this one, this one's status
    # flips to 'superseded') instead of overwriting live test logic in
    # place — see test_rule_service.update_test_rule.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    supersedes_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_rules.rule_id", ondelete="SET NULL")
    )
