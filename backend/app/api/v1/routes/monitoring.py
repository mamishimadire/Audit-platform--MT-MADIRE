import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.audit_test import AuditTest
from app.models.monitoring import MonitoringSchedule
from app.models.rbac import User
from app.schemas.audit_engine import MonitoringScheduleCreate, MonitoringScheduleOut
from app.services.monitoring_service import create_schedule, list_schedules, list_schedules_for_organization

router = APIRouter(tags=["monitoring"])


def _get_test_or_404(db: Session, audit_test_id: uuid.UUID) -> AuditTest:
    test = db.get(AuditTest, audit_test_id)
    if test is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit test not found")
    return test


@router.post(
    "/organizations/{organization_id}/audit-tests/{audit_test_id}/schedules",
    response_model=MonitoringScheduleOut,
    status_code=status.HTTP_201_CREATED,
)
def create(
    organization_id: uuid.UUID,
    audit_test_id: uuid.UUID,
    payload: MonitoringScheduleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> MonitoringSchedule:
    enforce_same_organization(organization_id, user, db)
    test = _get_test_or_404(db, audit_test_id)
    if test.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit test not found in this organization")
    return create_schedule(db, audit_test_id=audit_test_id, payload=payload, organization_id=organization_id, created_by_user_id=user.user_id)


@router.get("/organizations/{organization_id}/audit-tests/{audit_test_id}/schedules", response_model=list[MonitoringScheduleOut])
def list_all(
    organization_id: uuid.UUID, audit_test_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[MonitoringSchedule]:
    enforce_same_organization(organization_id, user, db)
    return list_schedules(db, audit_test_id=audit_test_id)


@router.get("/organizations/{organization_id}/schedules", response_model=list[MonitoringScheduleOut])
def list_all_for_organization(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[MonitoringSchedule]:
    enforce_same_organization(organization_id, user, db)
    return list_schedules_for_organization(db, organization_id=organization_id)
