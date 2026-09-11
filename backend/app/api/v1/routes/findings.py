import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.audit_test import AuditTest
from app.models.evidence_exception import Exception_
from app.models.finding import Finding, RemediationAction
from app.models.monitoring import TestExecution
from app.models.rbac import User
from app.schemas.finding import (
    FindingCreate,
    FindingOut,
    FindingTrace,
    RemediationActionCreate,
    RemediationActionOut,
    RemediationActionUpdate,
    RetestCreate,
    RetestOut,
    RootCauseCreate,
    RootCauseOut,
)
from app.services.finding_service import (
    create_finding,
    create_remediation_action,
    create_retest,
    create_root_cause,
    is_overdue,
    list_findings,
    list_remediation_actions,
    list_remediation_actions_for_organization,
    list_retests,
    list_retests_for_organization,
    list_root_causes,
    update_remediation_status,
)
from app.services.trace_service import trace_from_finding

router = APIRouter(tags=["findings"])


def _exception_organization(db: Session, exception_id: uuid.UUID) -> uuid.UUID:
    exception = db.get(Exception_, exception_id)
    if exception is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exception not found")
    execution = db.get(TestExecution, exception.execution_id)
    audit_test = db.get(AuditTest, execution.audit_test_id)
    return audit_test.organization_id


@router.post("/exceptions/{exception_id}/findings", response_model=FindingOut, status_code=status.HTTP_201_CREATED)
def escalate(
    exception_id: uuid.UUID, payload: FindingCreate, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> Finding:
    organization_id = _exception_organization(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    return create_finding(db, exception_id=exception_id, payload=payload, organization_id=organization_id, created_by_user_id=user.user_id)


@router.get("/organizations/{organization_id}/findings", response_model=list[FindingOut])
def list_all(organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Finding]:
    enforce_same_organization(organization_id, user, db)
    return list_findings(db, organization_id=organization_id)


def _get_finding_or_404(db: Session, finding_id: uuid.UUID) -> Finding:
    finding = db.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finding not found")
    return finding


@router.get("/findings/{finding_id}/trace", response_model=FindingTrace)
def trace(finding_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> FindingTrace:
    finding = _get_finding_or_404(db, finding_id)
    enforce_same_organization(finding.organization_id, user, db)
    return FindingTrace(chain=trace_from_finding(db, finding=finding))


@router.post("/findings/{finding_id}/root-causes", response_model=RootCauseOut, status_code=status.HTTP_201_CREATED)
def add_root_cause(
    finding_id: uuid.UUID, payload: RootCauseCreate, db: Session = Depends(get_db), user: User = Depends(require_permissions("audit_framework:manage"))
):
    finding = _get_finding_or_404(db, finding_id)
    enforce_same_organization(finding.organization_id, user, db)
    return create_root_cause(db, finding_id=finding_id, payload=payload)


@router.get("/findings/{finding_id}/root-causes", response_model=list[RootCauseOut])
def get_root_causes(finding_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    finding = _get_finding_or_404(db, finding_id)
    enforce_same_organization(finding.organization_id, user, db)
    return list_root_causes(db, finding_id=finding_id)


@router.post("/findings/{finding_id}/remediation-actions", response_model=RemediationActionOut, status_code=status.HTTP_201_CREATED)
def add_remediation(
    finding_id: uuid.UUID,
    payload: RemediationActionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
):
    finding = _get_finding_or_404(db, finding_id)
    enforce_same_organization(finding.organization_id, user, db)
    action = create_remediation_action(db, finding_id=finding_id, payload=payload, organization_id=finding.organization_id, created_by_user_id=user.user_id)
    out = RemediationActionOut.model_validate(action)
    return out.model_copy(update={"is_overdue": is_overdue(action.target_date, action.status)})


@router.get("/findings/{finding_id}/remediation-actions", response_model=list[RemediationActionOut])
def get_remediation(finding_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    finding = _get_finding_or_404(db, finding_id)
    enforce_same_organization(finding.organization_id, user, db)
    actions = list_remediation_actions(db, finding_id=finding_id)
    return [
        RemediationActionOut.model_validate(a).model_copy(update={"is_overdue": is_overdue(a.target_date, a.status)}) for a in actions
    ]


def _get_remediation_with_org(db: Session, remediation_id: uuid.UUID) -> tuple[RemediationAction, uuid.UUID]:
    action = db.get(RemediationAction, remediation_id)
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Remediation action not found")
    finding = db.get(Finding, action.finding_id)
    return action, finding.organization_id


@router.patch("/remediation-actions/{remediation_id}", response_model=RemediationActionOut)
def update_remediation(
    remediation_id: uuid.UUID,
    payload: RemediationActionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
):
    action, organization_id = _get_remediation_with_org(db, remediation_id)
    enforce_same_organization(organization_id, user, db)
    updated = update_remediation_status(db, action=action, status=payload.status, organization_id=organization_id, updated_by_user_id=user.user_id)
    out = RemediationActionOut.model_validate(updated)
    return out.model_copy(update={"is_overdue": is_overdue(updated.target_date, updated.status)})


@router.post("/findings/{finding_id}/retests", response_model=RetestOut, status_code=status.HTTP_201_CREATED)
def retest(
    finding_id: uuid.UUID, payload: RetestCreate, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
):
    finding = _get_finding_or_404(db, finding_id)
    enforce_same_organization(finding.organization_id, user, db)
    if finding.status != "awaiting_retest":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Finding is '{finding.status}', not 'awaiting_retest' — remediation must be marked completed first",
        )
    try:
        return create_retest(
            db, finding_id=finding_id, audit_test_id=payload.audit_test_id, result=payload.result, comments=payload.comments,
            organization_id=finding.organization_id, performed_by_user_id=user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.get("/findings/{finding_id}/retests", response_model=list[RetestOut])
def get_retests(finding_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    finding = _get_finding_or_404(db, finding_id)
    enforce_same_organization(finding.organization_id, user, db)
    return list_retests(db, finding_id=finding_id)


@router.get("/organizations/{organization_id}/remediation-actions", response_model=list[RemediationActionOut])
def list_all_remediation_actions(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    enforce_same_organization(organization_id, user, db)
    actions = list_remediation_actions_for_organization(db, organization_id=organization_id)
    return [
        RemediationActionOut.model_validate(a).model_copy(update={"is_overdue": is_overdue(a.target_date, a.status)}) for a in actions
    ]


@router.get("/organizations/{organization_id}/retests", response_model=list[RetestOut])
def list_all_retests(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    enforce_same_organization(organization_id, user, db)
    return list_retests_for_organization(db, organization_id=organization_id)
