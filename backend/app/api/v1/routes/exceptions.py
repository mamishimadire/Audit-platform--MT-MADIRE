import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.audit_test import AuditTest
from app.models.evidence_exception import EvidenceFile, EvidenceRequest, Exception_, ExceptionComment, ExceptionRecord
from app.models.monitoring import TestExecution
from app.models.rbac import Role, User, UserRole
from app.schemas.audit_engine import (
    EvidenceFileOut,
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
from app.services.auth_service import get_user_permission_names, get_user_role_names
from app.services.evidence_request_service import (
    create_request,
    delete_evidence_file as delete_evidence_file_row,
    list_requests_for_exception,
    upload_evidence,
)
from app.services.exception_comment_service import add_comment, list_comments
from app.services.exception_service import explain_exception, update_exception
from app.services.exception_trace_service import trace_from_exception
from app.services.execution_service import list_exceptions_for_organization
from sqlalchemy import select

router = APIRouter(tags=["exceptions"])


def _resolve_user_display(db: Session, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, tuple[str, str | None]]:
    """(name, first role) per user id, resolved directly against the users
    table rather than any org-scoped or platform-scoped users ENDPOINT —
    the only way to get a name for BOTH a client-side user and an internal
    auditor acting on the same exception, since no single users-list
    request a client-side viewer can make ever returns both."""
    if not user_ids:
        return {}
    names: dict[uuid.UUID, str] = {}
    for user_id, first_name, last_name in db.execute(
        select(User.user_id, User.first_name, User.last_name).where(User.user_id.in_(user_ids))
    ):
        names[user_id] = f"{first_name} {last_name}"
    roles: dict[uuid.UUID, str] = {}
    for user_id, role_name in db.execute(
        select(UserRole.user_id, Role.role_name).join(Role, Role.role_id == UserRole.role_id).where(UserRole.user_id.in_(user_ids))
    ):
        roles.setdefault(user_id, role_name)
    return {user_id: (names[user_id], roles.get(user_id)) for user_id in names}


def _to_evidence_requests_out(db: Session, requests: list[EvidenceRequest]) -> list[EvidenceRequestOut]:
    request_ids = [r.request_id for r in requests]
    files_by_request: dict[uuid.UUID, list[EvidenceFile]] = {rid: [] for rid in request_ids}
    all_files: list[EvidenceFile] = (
        list(
            db.scalars(
                select(EvidenceFile).where(EvidenceFile.request_id.in_(request_ids)).order_by(EvidenceFile.uploaded_at.asc())
            )
        )
        if request_ids
        else []
    )
    for f in all_files:
        files_by_request[f.request_id].append(f)

    user_ids = {r.requested_by for r in requests if r.requested_by} | {f.uploaded_by for f in all_files if f.uploaded_by}
    display = _resolve_user_display(db, user_ids)

    out = []
    for r in requests:
        requested = display.get(r.requested_by) if r.requested_by else None
        file_outs = []
        for f in files_by_request[r.request_id]:
            uploaded = display.get(f.uploaded_by) if f.uploaded_by else None
            file_outs.append(
                EvidenceFileOut.model_validate(f).model_copy(
                    update={
                        "uploaded_by_name": uploaded[0] if uploaded else None,
                        "uploaded_by_role": uploaded[1] if uploaded else None,
                    }
                )
            )
        out.append(
            EvidenceRequestOut.model_validate(r).model_copy(
                update={
                    "requested_by_name": requested[0] if requested else None,
                    "requested_by_role": requested[1] if requested else None,
                    "files": file_outs,
                }
            )
        )
    return out


def _to_comments_out(db: Session, comments: list[ExceptionComment]) -> list[ExceptionCommentOut]:
    display = _resolve_user_display(db, {c.author_id for c in comments if c.author_id})
    out = []
    for c in comments:
        author = display.get(c.author_id) if c.author_id else None
        out.append(
            ExceptionCommentOut.model_validate(c).model_copy(
                update={"author_name": author[0] if author else None, "author_role": author[1] if author else None}
            )
        )
    return out


def _is_unprivileged_exception_owner(db: Session, *, user: User) -> bool:
    """Exception Owner holds no standing permissions at all (see migration
    0005) — its whole job is resolving whatever's actually assigned to
    it, not an organization-wide view. Anyone who ALSO holds
    audit_framework:manage or exceptions:assign (the audit team, the
    client's own admin) is exempt — this only identifies the plain
    Exception Owner case, never any other role."""
    granted = get_user_permission_names(db, user.user_id)
    return "Exception Owner" in get_user_role_names(db, user.user_id) and granted.isdisjoint(
        {"audit_framework:manage", "exceptions:assign"}
    )


@router.get("/organizations/{organization_id}/exceptions", response_model=list[ExceptionOut])
def list_all(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[Exception_]:
    enforce_same_organization(organization_id, user, db)
    exceptions = list_exceptions_for_organization(db, organization_id=organization_id)
    if _is_unprivileged_exception_owner(db, user=user):
        exceptions = [e for e in exceptions if e.owner_id == user.user_id]
    return exceptions


def _get_exception_with_org(db: Session, exception_id: uuid.UUID) -> tuple[Exception_, uuid.UUID]:
    exception = db.get(Exception_, exception_id)
    if exception is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exception not found")
    execution = db.get(TestExecution, exception.execution_id)
    audit_test = db.get(AuditTest, execution.audit_test_id)
    return exception, audit_test.organization_id


def _require_exception_visible(db: Session, *, exception: Exception_, user: User) -> None:
    """Mirrors list_all's own filtering (_is_unprivileged_exception_owner)
    for the single-exception routes — otherwise a plain Exception Owner
    could see everything the list hides just by knowing/guessing another
    exception's id directly."""
    if _is_unprivileged_exception_owner(db, user=user) and exception.owner_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only view exceptions assigned to you.")


def _require_exception_collaborator(db: Session, *, exception: Exception_, user: User) -> None:
    """Comments and evidence requests are a private, exception-specific
    conversation between the audit team and whoever's actually assigned
    to it — not a shared noticeboard every member of the organization can
    read, the same way a WhatsApp thread between two people isn't visible
    to everyone else in the group. Being in the same organization (which
    enforce_same_organization already checked) is necessary but not
    sufficient; you also have to be one of the people actually on this
    exception."""
    if exception.owner_id == user.user_id:
        return
    granted = get_user_permission_names(db, user.user_id)
    if granted.isdisjoint({"audit_framework:manage", "exceptions:assign"}):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only this exception's assigned owner or the audit team can view or take part in its evidence requests and comments.",
        )


def _require_evidence_uploader(db: Session, *, exception: Exception_, user: User) -> None:
    """Uploading evidence is the client side fulfilling a request the audit
    team made of them — the auditor who asked for the document can only
    download what comes back as proof, not supply it themselves. Being a
    collaborator (already checked by the caller) is necessary but not
    sufficient here."""
    if exception.owner_id == user.user_id:
        return
    granted = get_user_permission_names(db, user.user_id)
    if "exceptions:assign" in granted:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only the client side (this exception's owner or the client organization's admin) can upload evidence. The audit team can only download it.",
    )


@router.get("/exceptions/{exception_id}", response_model=ExceptionOut)
def get_one(exception_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Exception_:
    exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    _require_exception_visible(db, exception=exception, user=user)
    return exception


@router.patch("/exceptions/{exception_id}", response_model=ExceptionOut)
def update(
    exception_id: uuid.UUID,
    payload: ExceptionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Exception_:
    exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)

    # owner_id=None alone can't tell "the caller didn't send this field"
    # apart from "the caller explicitly wants it cleared" — model_fields_set
    # is that signal. Without it, selecting "Unassigned" in the owner
    # dropdown would silently do nothing (owner_id already None either way).
    owner_id_provided = "owner_id" in payload.model_fields_set
    clear_owner = owner_id_provided and payload.owner_id is None

    granted = get_user_permission_names(db, user.user_id)
    # Assigning (or clearing) who owns an exception is the client
    # organization's own call, not the internal audit team's —
    # exceptions:assign is granted only to Client Organisation Admin (see
    # migration 0063). Status changes are a separate, broader action both
    # sides legitimately do, so audit_framework:manage still covers those.
    if owner_id_provided and "exceptions:assign" not in granted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the client organization's own admin (exceptions:assign) can assign an exception's owner.",
        )
    if payload.owner_id is not None:
        # Belt-and-braces server-side check to match the Exceptions page's
        # own dropdown filtering — a direct API call could otherwise
        # assign ownership to someone who can't even log in yet (still
        # awaiting approval/activation) or not at all anymore (deactivated,
        # removed), or who never holds the Exception Owner role to begin with.
        candidate = db.get(User, payload.owner_id)
        if (
            candidate is None
            or candidate.organization_id != organization_id
            or candidate.status != "active"
            or "Exception Owner" not in get_user_role_names(db, candidate.user_id)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The exception's owner must be an active user in this organization who holds the Exception Owner role.",
            )
    # The exception's own assigned owner can always move its status —
    # they're the one actually doing the work, and this is how "I fixed
    # it, mark it resolved" gets recorded at all for a role that holds
    # neither audit_framework:manage nor exceptions:assign. update_exception
    # itself still blocks the owner from CLOSING their own exception when
    # the organization's SoD setting is on.
    is_own_exception = exception.owner_id == user.user_id
    if payload.status is not None and granted.isdisjoint({"audit_framework:manage", "exceptions:assign"}) and not is_own_exception:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required permission(s): audit_framework:manage, exceptions:assign",
        )

    try:
        return update_exception(
            db, exception=exception, status=payload.status, owner_id=payload.owner_id, clear_owner=clear_owner,
            organization_id=organization_id, updated_by_user_id=user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.get("/exceptions/{exception_id}/records", response_model=list[ExceptionRecordOut])
def records(exception_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    _require_exception_visible(db, exception=exception, user=user)
    # Most recently detected first — a fast-re-detecting schedule piles up
    # one row per run, and the frontend only shows the latest by default
    # (the rest are available as history), so this order is what makes
    # "latest" a one-line records[0] instead of a client-side sort.
    return list(
        db.scalars(
            select(ExceptionRecord).where(ExceptionRecord.exception_id == exception_id).order_by(ExceptionRecord.detected_at.desc())
        )
    )


@router.get("/exceptions/{exception_id}/explanation", response_model=ExceptionExplanationOut)
def explanation(exception_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    _require_exception_visible(db, exception=exception, user=user)
    return explain_exception(db, exception=exception)


@router.get("/exceptions/{exception_id}/trace", response_model=ExceptionTraceOut)
def trace(exception_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ExceptionTraceOut:
    exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    _require_exception_visible(db, exception=exception, user=user)
    return trace_from_exception(db, exception=exception)


# --- Evidence requests: an auditor asks the client for one or more
# documents, the client uploads them, done. Requesting is auditor-only;
# uploading/deleting is client-only (_require_evidence_uploader) — the
# audit team can only download what comes back as proof. Viewing (both
# sides) is restricted to this exception's own assigned owner plus the
# audit team (_require_exception_collaborator) — NOT every member of the
# organization; this is a private, exception-specific conversation, not
# a shared noticeboard.


def _get_evidence_request_with_org(db: Session, request_id: uuid.UUID) -> tuple[EvidenceRequest, Exception_, uuid.UUID]:
    request = db.get(EvidenceRequest, request_id)
    if request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence request not found")
    exception, organization_id = _get_exception_with_org(db, request.exception_id)
    return request, exception, organization_id


def _get_evidence_file_with_org(
    db: Session, request_id: uuid.UUID, file_id: uuid.UUID
) -> tuple[EvidenceFile, EvidenceRequest, Exception_, uuid.UUID]:
    evidence_file = db.get(EvidenceFile, file_id)
    if evidence_file is None or evidence_file.request_id != request_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence file not found")
    request, exception, organization_id = _get_evidence_request_with_org(db, request_id)
    return evidence_file, request, exception, organization_id


@router.get("/exceptions/{exception_id}/evidence-requests", response_model=list[EvidenceRequestOut])
def list_evidence_requests(
    exception_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[EvidenceRequestOut]:
    exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    _require_exception_collaborator(db, exception=exception, user=user)
    return _to_evidence_requests_out(db, list_requests_for_exception(db, exception_id=exception_id))


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
) -> EvidenceRequestOut:
    _exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    created = create_request(db, exception_id=exception_id, payload=payload, organization_id=organization_id, requested_by_user_id=user.user_id)
    return _to_evidence_requests_out(db, [created])[0]


@router.post("/evidence-requests/{request_id}/upload", response_model=EvidenceRequestOut)
async def upload_evidence_route(
    request_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EvidenceRequestOut:
    request, exception, organization_id = _get_evidence_request_with_org(db, request_id)
    enforce_same_organization(organization_id, user, db)
    _require_exception_collaborator(db, exception=exception, user=user)
    _require_evidence_uploader(db, exception=exception, user=user)
    data = await file.read()
    try:
        updated = upload_evidence(
            db, request=request, file_name=file.filename or "upload", content_type=file.content_type,
            file_data=data, organization_id=organization_id, uploaded_by_user_id=user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_evidence_requests_out(db, [updated])[0]


@router.get("/evidence-requests/{request_id}/files/{file_id}")
def download_evidence_file(
    request_id: uuid.UUID, file_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> Response:
    evidence_file, _request, exception, organization_id = _get_evidence_file_with_org(db, request_id, file_id)
    enforce_same_organization(organization_id, user, db)
    _require_exception_collaborator(db, exception=exception, user=user)
    return Response(
        content=evidence_file.file_data,
        media_type=evidence_file.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{evidence_file.file_name}"'},
    )


@router.delete("/evidence-requests/{request_id}/files/{file_id}", response_model=EvidenceRequestOut)
def delete_evidence_file_route(
    request_id: uuid.UUID, file_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> EvidenceRequestOut:
    evidence_file, request, exception, organization_id = _get_evidence_file_with_org(db, request_id, file_id)
    enforce_same_organization(organization_id, user, db)
    _require_exception_collaborator(db, exception=exception, user=user)
    # Only the client side can undo their own upload — same rule as who
    # can upload in the first place (_require_evidence_uploader).
    _require_evidence_uploader(db, exception=exception, user=user)
    updated = delete_evidence_file_row(
        db, request=request, evidence_file=evidence_file, organization_id=organization_id, deleted_by_user_id=user.user_id
    )
    return _to_evidence_requests_out(db, [updated])[0]


# --- The auditor/client comment thread on one exception — same
# owner-or-audit-team-only access as evidence requests above.


@router.get("/exceptions/{exception_id}/comments", response_model=list[ExceptionCommentOut])
def list_exception_comments(
    exception_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[ExceptionCommentOut]:
    exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    _require_exception_collaborator(db, exception=exception, user=user)
    return _to_comments_out(db, list_comments(db, exception_id=exception_id))


@router.post(
    "/exceptions/{exception_id}/comments", response_model=ExceptionCommentOut, status_code=status.HTTP_201_CREATED
)
def add_exception_comment(
    exception_id: uuid.UUID,
    payload: ExceptionCommentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ExceptionCommentOut:
    exception, organization_id = _get_exception_with_org(db, exception_id)
    enforce_same_organization(organization_id, user, db)
    _require_exception_collaborator(db, exception=exception, user=user)
    try:
        created = add_comment(db, exception_id=exception_id, body=payload.body, organization_id=organization_id, author_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_comments_out(db, [created])[0]
