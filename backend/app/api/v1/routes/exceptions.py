import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.audit_test import AuditTest
from app.models.evidence_exception import Exception_, ExceptionRecord
from app.models.monitoring import TestExecution
from app.models.rbac import User
from app.schemas.audit_engine import ExceptionOut, ExceptionRecordOut, ExceptionUpdate
from app.services.exception_service import update_exception
from app.services.execution_service import list_exceptions_for_organization
from sqlalchemy import select

router = APIRouter(tags=["exceptions"])


@router.get("/organizations/{organization_id}/exceptions", response_model=list[ExceptionOut])
def list_all(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[Exception_]:
    enforce_same_organization(organization_id, user, db)
    return list_exceptions_for_organization(db, organization_id=organization_id)


def _get_exception_with_org(db: Session, exception_id: uuid.UUID) -> tuple[Exception_, uuid.UUID]:
    exception = db.get(Exception_, exception_id)
    if exception is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exception not found")
    execution = db.get(TestExecution, exception.execution_id)
    audit_test = db.get(AuditTest, execution.audit_test_id)
    return exception, audit_test.organization_id


@router.patch("/exceptions/{exception_id}", response_model=ExceptionOut)
def update(
    exception_id: uuid.UUID,
    payload: ExceptionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> Exception_:
    exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    try:
        return update_exception(
            db, exception=exception, status=payload.status, owner_id=payload.owner_id, organization_id=organization_id,
            updated_by_user_id=user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.get("/exceptions/{exception_id}/records", response_model=list[ExceptionRecordOut])
def records(exception_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    return list(db.scalars(select(ExceptionRecord).where(ExceptionRecord.exception_id == exception_id)))
