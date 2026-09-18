import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permissions
from app.db.session import get_db
from app.models.rbac import User
from app.schemas.user import PlatformUserCreate, UserOut, UserReasonRequest
from app.services.auth_service import get_role_names_bulk, get_user_role_names
from app.services.user_service import (
    approve_deactivation,
    approve_pending_user,
    approve_removal,
    cancel_deactivation,
    cancel_pending_user,
    cancel_removal,
    create_platform_user,
    list_platform_users,
    reject_deactivation,
    reject_pending_user,
    reject_removal,
    request_deactivation,
    request_removal,
)

router = APIRouter(prefix="/platform/users", tags=["platform-users"])


def _to_out(db: Session, user: User) -> UserOut:
    out = UserOut.model_validate(user)
    return out.model_copy(
        update={"roles": get_user_role_names(db, user.user_id), "temporary_password": user.temporary_password_plaintext}
    )


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create(
    payload: PlatformUserCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("organizations:manage")),
) -> UserOut:
    # organizations:manage is already restricted to Platform Super Admin /
    # Platform Admin, and a client user never holds it — but a client user
    # can never reach this route logically anyway, since it creates
    # organization_id=NULL accounts. Belt-and-braces guard regardless.
    if user.organization_id is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only internal (platform) users can add internal users")
    try:
        created = create_platform_user(db, payload=payload, created_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, created)


@router.get("", response_model=list[UserOut])
def list_all(
    db: Session = Depends(get_db), user: User = Depends(require_permissions("organizations:manage"))
) -> list[UserOut]:
    if user.organization_id is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only internal (platform) users can view internal users")
    users = list_platform_users(db)
    roles_by_user = get_role_names_bulk(db, [u.user_id for u in users])
    out = []
    for u in users:
        row = UserOut.model_validate(u)
        out.append(
            row.model_copy(
                update={"roles": roles_by_user.get(u.user_id, []), "temporary_password": u.temporary_password_plaintext}
            )
        )
    return out


def _get_platform_user_or_404(db: Session, user_id: uuid.UUID) -> User:
    target = db.get(User, user_id)
    if target is None or target.organization_id is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return target


@router.post("/{user_id}/approve", response_model=UserOut)
def approve(
    user_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_permissions("organizations:manage"))
) -> UserOut:
    target = _get_platform_user_or_404(db, user_id)
    try:
        approved = approve_pending_user(db, user=target, approved_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, approved)


@router.post("/{user_id}/reject", response_model=UserOut)
def reject(
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("organizations:manage")),
) -> UserOut:
    target = _get_platform_user_or_404(db, user_id)
    try:
        rejected = reject_pending_user(db, user=target, reason=payload.reason, rejected_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, rejected)


@router.post("/{user_id}/cancel", response_model=UserOut)
def cancel(
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("organizations:manage")),
) -> UserOut:
    target = _get_platform_user_or_404(db, user_id)
    try:
        cancelled = cancel_pending_user(db, user=target, reason=payload.reason, cancelled_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, cancelled)


@router.post("/{user_id}/deactivation/request", response_model=UserOut)
def request_deactivation_route(
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("organizations:manage")),
) -> UserOut:
    target = _get_platform_user_or_404(db, user_id)
    try:
        updated = request_deactivation(db, user=target, reason=payload.reason, requested_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{user_id}/deactivation/approve", response_model=UserOut)
def approve_deactivation_route(
    user_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_permissions("organizations:manage"))
) -> UserOut:
    target = _get_platform_user_or_404(db, user_id)
    try:
        updated = approve_deactivation(db, user=target, approved_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{user_id}/deactivation/reject", response_model=UserOut)
def reject_deactivation_route(
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("organizations:manage")),
) -> UserOut:
    target = _get_platform_user_or_404(db, user_id)
    try:
        updated = reject_deactivation(db, user=target, reason=payload.reason, rejected_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{user_id}/deactivation/cancel", response_model=UserOut)
def cancel_deactivation_route(
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("organizations:manage")),
) -> UserOut:
    target = _get_platform_user_or_404(db, user_id)
    try:
        updated = cancel_deactivation(db, user=target, reason=payload.reason, cancelled_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{user_id}/removal/request", response_model=UserOut)
def request_removal_route(
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("organizations:manage")),
) -> UserOut:
    target = _get_platform_user_or_404(db, user_id)
    try:
        updated = request_removal(db, user=target, reason=payload.reason, requested_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{user_id}/removal/approve", response_model=UserOut)
def approve_removal_route(
    user_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_permissions("organizations:manage"))
) -> UserOut:
    target = _get_platform_user_or_404(db, user_id)
    try:
        updated = approve_removal(db, user=target, approved_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{user_id}/removal/reject", response_model=UserOut)
def reject_removal_route(
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("organizations:manage")),
) -> UserOut:
    target = _get_platform_user_or_404(db, user_id)
    try:
        updated = reject_removal(db, user=target, reason=payload.reason, rejected_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{user_id}/removal/cancel", response_model=UserOut)
def cancel_removal_route(
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("organizations:manage")),
) -> UserOut:
    target = _get_platform_user_or_404(db, user_id)
    try:
        updated = cancel_removal(db, user=target, reason=payload.reason, cancelled_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, updated)
