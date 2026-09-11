import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.rbac import User
from app.models.risk_control import Risk
from app.schemas.risk import RiskCategoryOut, RiskCreate, RiskOut, RiskUpdate
from app.services.risk_service import (
    create_risk,
    get_risk_ids_with_active_control,
    list_risk_categories,
    list_risks,
    update_risk,
)

router = APIRouter(tags=["risks"])


def _to_out(risk: Risk, active_control_risk_ids: set[uuid.UUID]) -> RiskOut:
    out = RiskOut.model_validate(risk)
    return out.model_copy(update={"has_active_control": risk.risk_id in active_control_risk_ids})


@router.get("/reference/risk-categories", response_model=list[RiskCategoryOut])
def list_categories(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return list_risk_categories(db)


@router.post("/organizations/{organization_id}/risks", response_model=RiskOut, status_code=201)
def create(
    organization_id: uuid.UUID,
    payload: RiskCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> RiskOut:
    enforce_same_organization(organization_id, user, db)
    risk = create_risk(db, organization_id=organization_id, payload=payload, created_by_user_id=user.user_id)
    return _to_out(risk, set())


@router.get("/organizations/{organization_id}/risks", response_model=list[RiskOut])
def list_all(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[RiskOut]:
    enforce_same_organization(organization_id, user, db)
    active_control_risk_ids = get_risk_ids_with_active_control(db, organization_id=organization_id)
    return [_to_out(r, active_control_risk_ids) for r in list_risks(db, organization_id=organization_id)]


@router.patch("/organizations/{organization_id}/risks/{risk_id}", response_model=RiskOut)
def update(
    organization_id: uuid.UUID,
    risk_id: uuid.UUID,
    payload: RiskUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> RiskOut:
    enforce_same_organization(organization_id, user, db)
    risk = db.get(Risk, risk_id)
    if risk is None or risk.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Risk not found")
    updated = update_risk(db, risk=risk, payload=payload, updated_by_user_id=user.user_id)
    active_control_risk_ids = get_risk_ids_with_active_control(db, organization_id=organization_id)
    return _to_out(updated, active_control_risk_ids)
