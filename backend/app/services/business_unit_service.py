import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.organization import BusinessUnit
from app.schemas.business_unit import BusinessUnitCreate
from app.services.audit_log_service import log_action


def create_business_unit(
    db: Session, *, organization_id: uuid.UUID, payload: BusinessUnitCreate, created_by_user_id: uuid.UUID
) -> BusinessUnit:
    unit = BusinessUnit(
        organization_id=organization_id,
        business_unit_name=payload.business_unit_name,
        business_unit_code=payload.business_unit_code,
        parent_business_unit_id=payload.parent_business_unit_id,
        status="active",
    )
    db.add(unit)
    db.flush()
    log_action(
        db,
        action=f"Created business unit '{unit.business_unit_name}'",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="business_units",
        entity_id=unit.business_unit_id,
        new_value={"business_unit_name": unit.business_unit_name},
    )
    db.commit()
    db.refresh(unit)
    return unit


def list_business_units(db: Session, *, organization_id: uuid.UUID) -> list[BusinessUnit]:
    return list(db.scalars(select(BusinessUnit).where(BusinessUnit.organization_id == organization_id)))
