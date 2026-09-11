import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, uuid_pk


class RiskCategory(Base):
    __tablename__ = "risk_categories"

    risk_category_id: Mapped[uuid.UUID] = uuid_pk("risk_category_id")
    category_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)


class Risk(Base, TimestampMixin):
    __tablename__ = "risks"

    risk_id: Mapped[uuid.UUID] = uuid_pk("risk_id")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    risk_library_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("risk_library.risk_library_id", ondelete="SET NULL")
    )
    process_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_processes.process_id", ondelete="SET NULL")
    )
    risk_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("risk_categories.risk_category_id", ondelete="SET NULL")
    )
    risk_name: Mapped[str] = mapped_column(String(255), nullable=False)
    risk_description: Mapped[str | None] = mapped_column(Text)
    inherent_risk_rating: Mapped[str | None] = mapped_column(String(20))
    residual_risk_rating: Mapped[str | None] = mapped_column(String(20))
    risk_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )


class Control(Base, TimestampMixin):
    __tablename__ = "controls"

    control_id: Mapped[uuid.UUID] = uuid_pk("control_id")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    control_library_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("control_library.control_library_id", ondelete="SET NULL")
    )
    control_code: Mapped[str | None] = mapped_column(String(50))
    control_name: Mapped[str] = mapped_column(String(255), nullable=False)
    control_description: Mapped[str | None] = mapped_column(Text)
    control_type: Mapped[str | None] = mapped_column(String(20))
    control_nature: Mapped[str | None] = mapped_column(String(20))
    control_frequency: Mapped[str | None] = mapped_column(String(30))
    control_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    # 'pending_mapping' | 'pending_activation' | 'active' | 'pending_deactivation' | 'inactive' | 'retired'
    # Activation and deactivation each go through a request/approve pair —
    # see control_service.request_activation/approve_activation and
    # request_deactivation/approve_deactivation. Deactivation approval is
    # mandatory (never skippable): a control that monitors something
    # shouldn't be silently turned off by the same person who wanted it off.
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    activation_requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    activation_approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    deactivation_requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    deactivation_requested_reason: Mapped[str | None] = mapped_column(Text)
    deactivation_approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )


class RiskControl(Base):
    __tablename__ = "risk_controls"
    __table_args__ = (UniqueConstraint("risk_id", "control_id"),)

    risk_control_id: Mapped[uuid.UUID] = uuid_pk("risk_control_id")
    risk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("risks.risk_id", ondelete="CASCADE"), nullable=False
    )
    control_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("controls.control_id", ondelete="CASCADE"), nullable=False
    )
