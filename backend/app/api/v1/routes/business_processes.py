import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.business_process import BusinessProcess
from app.models.rbac import User
from app.schemas.business_process import (
    BusinessProcessCreate,
    BusinessProcessOut,
    ProcessActivityCreate,
    ProcessActivityOut,
)
from app.services.business_process_service import (
    create_business_process,
    create_process_activity,
    list_business_processes,
    list_process_activities,
)

router = APIRouter(tags=["business-processes"])


@router.post("/organizations/{organization_id}/business-processes", response_model=BusinessProcessOut, status_code=201)
def create(
    organization_id: uuid.UUID,
    payload: BusinessProcessCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> BusinessProcess:
    enforce_same_organization(organization_id, user, db)
    return create_business_process(db, organization_id=organization_id, payload=payload, created_by_user_id=user.user_id)


@router.get("/organizations/{organization_id}/business-processes", response_model=list[BusinessProcessOut])
def list_all(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[BusinessProcess]:
    enforce_same_organization(organization_id, user, db)
    return list_business_processes(db, organization_id=organization_id)


def _get_process_or_404(db: Session, process_id: uuid.UUID) -> BusinessProcess:
    process = db.get(BusinessProcess, process_id)
    if process is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business process not found")
    return process


@router.post("/business-processes/{process_id}/activities", response_model=ProcessActivityOut, status_code=201)
def create_activity(
    process_id: uuid.UUID,
    payload: ProcessActivityCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
):
    process = _get_process_or_404(db, process_id)
    enforce_same_organization(process.organization_id, user, db)
    return create_process_activity(
        db,
        process_id=process_id,
        payload=payload,
        created_by_user_id=user.user_id,
        organization_id=process.organization_id,
    )


@router.get("/business-processes/{process_id}/activities", response_model=list[ProcessActivityOut])
def list_activities(process_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    process = _get_process_or_404(db, process_id)
    enforce_same_organization(process.organization_id, user, db)
    return list_process_activities(db, process_id=process_id)
