import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, uuid_pk


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    organization_id: Mapped[uuid.UUID] = uuid_pk("organization_id")
    organization_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trading_name: Mapped[str | None] = mapped_column(String(255))
    registration_number: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="onboarding")
    # True for exactly one seeded row: Madire's own organization, holding its
    # internal users/devices/gateways for dogfooding and platform-team use.
    # Never settable through the normal client-onboarding endpoint (that
    # payload has no is_internal field) — the only way this is ever true is
    # the one row the migration seeds.
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"))


class OrganizationSetting(Base, TimestampMixin):
    __tablename__ = "organization_settings"
    __table_args__ = (UniqueConstraint("organization_id", "setting_name"),)

    setting_id: Mapped[uuid.UUID] = uuid_pk("setting_id")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    setting_name: Mapped[str] = mapped_column(String(150), nullable=False)
    setting_value: Mapped[str | None] = mapped_column(Text)


class BusinessUnit(Base, TimestampMixin):
    __tablename__ = "business_units"

    business_unit_id: Mapped[uuid.UUID] = uuid_pk("business_unit_id")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    parent_business_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_units.business_unit_id", ondelete="SET NULL")
    )
    business_unit_name: Mapped[str] = mapped_column(String(255), nullable=False)
    business_unit_code: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
