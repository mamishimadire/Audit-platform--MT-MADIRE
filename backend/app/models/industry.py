import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, uuid_pk


class Industry(Base):
    __tablename__ = "industries"

    industry_id: Mapped[uuid.UUID] = uuid_pk("industry_id")
    industry_name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)


class OrganizationIndustry(Base):
    __tablename__ = "organization_industries"
    __table_args__ = (UniqueConstraint("organization_id", "industry_id"),)

    organization_industry_id: Mapped[uuid.UUID] = uuid_pk("organization_industry_id")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    industry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("industries.industry_id", ondelete="CASCADE"), nullable=False
    )
