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


def get_industry_names_bulk(db: Session, organization_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    """Batched equivalent of calling get_industry_names_for_organization
    once per organization — one query total instead of one per org, so an
    N-organization list page (which at "thousands of clients" scale is not
    hypothetical) doesn't pay N round trips just for the industries column."""
    if not organization_ids:
        return {}
    rows = db.execute(
        select(OrganizationIndustry.organization_id, Industry.industry_name)
        .join(Industry, Industry.industry_id == OrganizationIndustry.industry_id)
        .where(OrganizationIndustry.organization_id.in_(organization_ids))
        .order_by(Industry.industry_name)
    )
    result: dict[uuid.UUID, list[str]] = {oid: [] for oid in organization_ids}
    for org_id, industry_name in rows:
        result[org_id].append(industry_name)
    return result
