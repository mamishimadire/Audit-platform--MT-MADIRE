import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_any_permission, require_permissions
from app.db.session import get_db
from app.models.audit_test import AuditTest
from app.models.evidence_exception import EvidenceRequest, Exception_, ExceptionComment, ExceptionRecord
from app.models.monitoring import TestExecution
from app.models.rbac import User
from app.schemas.audit_engine import (
    EvidenceRequestCreate,
    EvidenceRequestOut,
    ExceptionCommentCreate,
    ExceptionCommentOut,
    ExceptionExplanationOut,
    ExceptionOut,
    ExceptionRecordOut,
    ExceptionTraceOut,
    ExceptionUpdate,
)
from app.services.evidence_request_service import create_request, list_requests_for_exception, upload_evidence
from app.services.exception_comment_service import add_comment, list_comments
from app.services.exception_service import explain_exception, update_exception
from app.services.exception_trace_service import trace_from_exception
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


@router.get("/exceptions/{exception_id}", response_model=ExceptionOut)
def get_one(exception_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Exception_:
    exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    return exception


@router.patch("/exceptions/{exception_id}", response_model=ExceptionOut)
def update(
    exception_id: uuid.UUID,
    payload: ExceptionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission("audit_framework:manage", "exceptions:assign")),
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


@router.get("/exceptions/{exception_id}/explanation", response_model=ExceptionExplanationOut)
def explanation(exception_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    return explain_exception(db, exception=exception)


@router.get("/exceptions/{exception_id}/trace", response_model=ExceptionTraceOut)
def trace(exception_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ExceptionTraceOut:
    exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    return trace_from_exception(db, exception=exception)


# --- Evidence requests: an auditor asks the client for one specific
# document, the client uploads it, done. Requesting is restricted (an
# internal auditor decides what's needed); uploading and viewing are open
# to anyone in the organization — the whole point is the client's own
# side responding, and it's no more sensitive than the exception itself
# they already have full access to.


def _get_evidence_request_with_org(db: Session, request_id: uuid.UUID) -> tuple[EvidenceRequest, uuid.UUID]:
    request = db.get(EvidenceRequest, request_id)
    if request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence request not found")
    _exception, organization_id = _get_exception_with_org(db, request.exception_id)
    return request, organization_id


@router.get("/exceptions/{exception_id}/evidence-requests", response_model=list[EvidenceRequestOut])
def list_evidence_requests(
    exception_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[EvidenceRequest]:
    _exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    return list_requests_for_exception(db, exception_id=exception_id)


@router.post(
    "/exceptions/{exception_id}/evidence-requests",
    response_model=EvidenceRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def request_evidence(
    exception_id: uuid.UUID,
    payload: EvidenceRequestCreate,
    db: Session = Depends(get_db),
    # Auditor-only, unlike PATCH /exceptions/{id} and the remediation-action
    # endpoints — this is the auditor asking the client for something, not
    # the client-side assignment action exceptions:assign was added for.
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> EvidenceRequest:
    _exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    return create_request(db, exception_id=exception_id, payload=payload, organization_id=organization_id, requested_by_user_id=user.user_id)


@router.post("/evidence-requests/{request_id}/upload", response_model=EvidenceRequestOut)
async def upload_evidence_route(
    request_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EvidenceRequest:
    request, organization_id = _get_evidence_request_with_org(db, request_id)
    enforce_same_organization(organization_id, user, db)
    data = await file.read()
    try:
        return upload_evidence(
            db, request=request, file_name=file.filename or "upload", content_type=file.content_type,
            file_data=data, organization_id=organization_id, uploaded_by_user_id=user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/evidence-requests/{request_id}/file")
def download_evidence_file(
    request_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> Response:
    request, organization_id = _get_evidence_request_with_org(db, request_id)
    enforce_same_organization(organization_id, user, db)
    if request.file_data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No file has been uploaded for this request yet")
    return Response(
        content=request.file_data,
        media_type=request.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{request.file_name or "evidence"}"'},
    )


# --- The auditor/client comment thread on one exception — same
# organization-wide read/write access as evidence requests above.


@router.get("/exceptions/{exception_id}/comments", response_model=list[ExceptionCommentOut])
def list_exception_comments(
    exception_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[ExceptionComment]:
    _exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    return list_comments(db, exception_id=exception_id)


@router.post(
    "/exceptions/{exception_id}/comments", response_model=ExceptionCommentOut, status_code=status.HTTP_201_CREATED
)
def add_exception_comment(
    exception_id: uuid.UUID,
    payload: ExceptionCommentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ExceptionComment:
    _exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    try:
        return add_comment(db, exception_id=exception_id, body=payload.body, organization_id=organization_id, author_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
