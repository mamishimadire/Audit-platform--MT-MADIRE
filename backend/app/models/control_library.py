import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, uuid_pk


class ControlLibraryEntry(Base):
    """
    Global, pre-built control catalogue — the ~20-domain / 150+ control set
    that ships with the platform. Not organization-scoped: every client
    browses the same list and "activates" the controls relevant to them
    (see Control.control_library_id) instead of typing controls in by hand.
    """

    __tablename__ = "control_library"

    control_library_id: Mapped[uuid.UUID] = uuid_pk("control_library_id")
    domain: Mapped[str] = mapped_column(String(100), nullable=False)
    control_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    control_name: Mapped[str] = mapped_column(String(255), nullable=False)
    audit_procedure: Mapped[str] = mapped_column(Text, nullable=False)
    required_tables: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    default_control_type: Mapped[str | None] = mapped_column(String(20))
    default_control_nature: Mapped[str | None] = mapped_column(String(20))
    default_control_frequency: Mapped[str | None] = mapped_column(String(30))


class ControlTableBinding(Base):
    """
    Per-organization: which actual discovered table satisfies one of a
    control's required canonical tables (e.g. hr_employees -> Finance
    DB.EMP_MASTER) — or an explicit, reasoned "not applicable to this
    entity." Gates activation: see control_service.set_control_status.
    """

    __tablename__ = "control_table_bindings"

    binding_id: Mapped[uuid.UUID] = uuid_pk("binding_id")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    control_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("controls.control_id", ondelete="CASCADE"), nullable=False
    )
    canonical_table_name: Mapped[str] = mapped_column(String(100), nullable=False)
    data_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.data_source_id", ondelete="SET NULL")
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_entities.entity_id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="bound")
    not_applicable_reason: Mapped[str | None] = mapped_column(Text)
    bound_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ControlRuleTemplate(Base):
    """
    A control-library entry's default test logic, in the exact same
    rule_definition shape test_rules already stores — auto-generating a
    rule is just copying this JSON into a new TestRule row, tagged
    origin='auto_generated'. Deliberately NOT seeded for every control —
    see control_rule_template_data.py for which ones have a real,
    human-verified template versus none at all.
    """

    __tablename__ = "control_rule_templates"

    template_id: Mapped[uuid.UUID] = uuid_pk("template_id")
    control_library_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("control_library.control_library_id", ondelete="CASCADE"), nullable=False, unique=True
    )
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_definition: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
