import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.organization import BusinessUnit
from app.models.rbac import User
from app.schemas.business_unit import BusinessUnitCreate, BusinessUnitOut
from app.services.business_unit_service import create_business_unit, list_business_units

router = APIRouter(prefix="/organizations/{organization_id}/business-units", tags=["business-units"])


@router.post("", response_model=BusinessUnitOut, status_code=201)
def create(
    organization_id: uuid.UUID,
    payload: BusinessUnitCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("business_units:manage")),
) -> BusinessUnit:
    enforce_same_organization(organization_id, user, db)
    return create_business_unit(db, organization_id=organization_id, payload=payload, created_by_user_id=user.user_id)


@router.get("", response_model=list[BusinessUnitOut])
def list_all(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[BusinessUnit]:
    enforce_same_organization(organization_id, user, db)
    return list_business_units(db, organization_id=organization_id)
