import uuid
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_gateway, get_current_user
from app.db.session import get_db
from app.models.data_source import Gateway
from app.models.rbac import User
from app.schemas.audit_engine import DueTest, EvidenceOut, ExecutionReport, TestExecutionOut
from app.services.execution_service import (
    list_evidence_for_organization,
    list_executions,
    list_executions_for_organization,
    record_execution_report,
    resolve_due_tests_for_gateway,
)

router = APIRouter(tags=["executions"])


@router.get("/gateways/{gateway_id}/due-tests", response_model=list[DueTest])
def due_tests(gateway_id: uuid.UUID, db: Session = Depends(get_db), gateway: Gateway = Depends(get_current_gateway)):
    # get_current_gateway already authenticates by (gateway_id, api_key) pair from headers;
    # the path param is compared for defense in depth, matching the other gateway routes.
    return resolve_due_tests_for_gateway(db, gateway_id=gateway.gateway_id)


@router.post("/gateways/{gateway_id}/execution-reports", response_model=TestExecutionOut, status_code=201)
def report_execution(
    gateway_id: uuid.UUID, payload: ExecutionReport, db: Session = Depends(get_db), gateway: Gateway = Depends(get_current_gateway)
):
    return record_execution_report(db, report=payload)


@router.get("/organizations/{organization_id}/audit-tests/{audit_test_id}/executions", response_model=list[TestExecutionOut])
def list_all(
    organization_id: uuid.UUID, audit_test_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    enforce_same_organization(organization_id, user, db)
    return list_executions(db, audit_test_id=audit_test_id)


@router.get("/organizations/{organization_id}/executions", response_model=list[TestExecutionOut])
def list_all_for_organization(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    enforce_same_organization(organization_id, user, db)
    return list_executions_for_organization(db, organization_id=organization_id)


@router.get("/organizations/{organization_id}/evidence", response_model=list[EvidenceOut])
def list_evidence(
    organization_id: uuid.UUID,
    from_date: date | None = None,
    to_date: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    enforce_same_organization(organization_id, user, db)
    from_dt = datetime.combine(from_date, time.min, tzinfo=timezone.utc) if from_date else None
    to_dt = datetime.combine(to_date, time.max, tzinfo=timezone.utc) if to_date else None
    return list_evidence_for_organization(db, organization_id=organization_id, from_date=from_dt, to_date=to_dt)
