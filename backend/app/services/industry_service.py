import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.industry import Industry, OrganizationIndustry


def list_industries(db: Session) -> list[Industry]:
    return list(db.scalars(select(Industry).order_by(Industry.industry_name)))


def get_industry_names_for_organization(db: Session, *, organization_id: uuid.UUID) -> list[str]:
    stmt = (
        select(Industry.industry_name)
        .join(OrganizationIndustry, OrganizationIndustry.industry_id == Industry.industry_id)
        .where(OrganizationIndustry.organization_id == organization_id)
        .order_by(Industry.industry_name)
    )
    return list(db.scalars(stmt))
