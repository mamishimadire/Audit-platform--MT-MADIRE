import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.industry import Industry
from app.models.risk_control import Risk, RiskCategory
from app.models.risk_library import RiskLibraryEntry


def _get_or_create_category(db: Session, *, category_name: str) -> RiskCategory:
    category = db.scalar(select(RiskCategory).where(RiskCategory.category_name == category_name))
    if category is None:
        category = RiskCategory(category_name=category_name)
        db.add(category)
        db.flush()
    return category


def auto_seed_risks_for_organization(
    db: Session, *, organization_id: uuid.UUID, industry_ids: list[uuid.UUID], created_by_user_id: uuid.UUID
) -> list[Risk]:
    """
    Builds the organization's starting risk register from the risk library:
    every generic (industry_id IS NULL) entry, plus every entry tagged with
    one of the organization's selected industries. Nothing here is final —
    each created risk stays fully editable (see risk_service.update_risk).
    """
    stmt = select(RiskLibraryEntry).where(
        (RiskLibraryEntry.industry_id.is_(None)) | (RiskLibraryEntry.industry_id.in_(industry_ids))
    )
    entries = list(db.scalars(stmt))

    created: list[Risk] = []
    for entry in entries:
        category = _get_or_create_category(db, category_name=entry.category_name)
        risk = Risk(
            organization_id=organization_id,
            risk_library_id=entry.risk_library_id,
            risk_category_id=category.risk_category_id,
            risk_name=entry.risk_name,
            risk_description=entry.risk_description,
            inherent_risk_rating=entry.default_inherent_risk_rating,
            status="active",
            created_by=created_by_user_id,
        )
        db.add(risk)
        created.append(risk)

    db.flush()
    return created


def validate_industry_ids(db: Session, *, industry_ids: list[uuid.UUID]) -> None:
    if not industry_ids:
        return
    found = set(db.scalars(select(Industry.industry_id).where(Industry.industry_id.in_(industry_ids))))
    missing = set(industry_ids) - found
    if missing:
        raise ValueError(f"Unknown industry id(s): {', '.join(str(i) for i in missing)}")
