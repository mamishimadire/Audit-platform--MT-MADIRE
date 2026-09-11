import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, uuid_pk


class RiskLibraryEntry(Base):
    """
    Global, pre-built risk catalogue — mirrors control_library.py. A NULL
    industry_id means the risk applies to every organization regardless of
    industry (see risk_library_data.py::GENERIC_RISK_LIBRARY); a set
    industry_id means it only gets auto-created for organizations that
    selected that industry.
    """

    __tablename__ = "risk_library"

    risk_library_id: Mapped[uuid.UUID] = uuid_pk("risk_library_id")
    industry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("industries.industry_id", ondelete="CASCADE")
    )
    category_name: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_name: Mapped[str] = mapped_column(String(255), nullable=False)
    risk_description: Mapped[str] = mapped_column(Text, nullable=False)
    default_inherent_risk_rating: Mapped[str] = mapped_column(String(20), nullable=False)
