import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.rbac import User
from app.schemas.user import UserCreate, UserOut, UserReasonRequest
from app.services.auth_service import get_role_names_bulk, get_user_permission_names, get_user_role_names
from app.services.user_service import (
    approve_deactivation,
    approve_pending_user,
    approve_removal,
    cancel_deactivation,
    cancel_pending_user,
    cancel_removal,
    create_user_in_organization,
    reject_deactivation,
    reject_pending_user,
    reject_removal,
    request_deactivation,
    request_removal,
)

router = APIRouter(prefix="/organizations/{organization_id}/users", tags=["users"])


def _to_out(db: Session, user: User) -> UserOut:
    out = UserOut.model_validate(user)
    return out.model_copy(
        update={
            "roles": get_user_role_names(db, user.user_id),
            "temporary_password": user.temporary_password_plaintext,
        }
    )


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    organization_id: uuid.UUID,
    payload: UserCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("users:manage")),
) -> UserOut:
    enforce_same_organization(organization_id, user, db)
    try:
        created = create_user_in_organization(
            db, organization_id=organization_id, payload=payload, created_by_user_id=user.user_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, created)


@router.get("", response_model=list[UserOut])
def list_users(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[UserOut]:
    enforce_same_organization(organization_id, user, db)
    can_see_temporary_passwords = "users:manage" in get_user_permission_names(db, user.user_id)
    stmt = select(User).where(User.organization_id == organization_id)
    users = list(db.scalars(stmt))
    roles_by_user = get_role_names_bulk(db, [u.user_id for u in users])
    out = []
    for u in users:
        row = UserOut.model_validate(u)
        out.append(
            row.model_copy(
                update={
                    "roles": roles_by_user.get(u.user_id, []),
                    "temporary_password": u.temporary_password_plaintext if can_see_temporary_passwords else None,
                }
            )
        )
    return out


def _get_org_user_or_404(db: Session, organization_id: uuid.UUID, user_id: uuid.UUID) -> User:
    target = db.get(User, user_id)
    if target is None or target.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return target


@router.post("/{user_id}/approve", response_model=UserOut)
def approve_user(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("users:manage")),
) -> UserOut:
    enforce_same_organization(organization_id, user, db)
    target = _get_org_user_or_404(db, organization_id, user_id)
    try:
        approved = approve_pending_user(db, user=target, approved_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, approved)


@router.post("/{user_id}/reject", response_model=UserOut)
def reject_user(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("users:manage")),
) -> UserOut:
    enforce_same_organization(organization_id, user, db)
    target = _get_org_user_or_404(db, organization_id, user_id)
    try:
        rejected = reject_pending_user(db, user=target, reason=payload.reason, rejected_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, rejected)


@router.post("/{user_id}/cancel", response_model=UserOut)
def cancel_user(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("users:manage")),
) -> UserOut:
    enforce_same_organization(organization_id, user, db)
    target = _get_org_user_or_404(db, organization_id, user_id)
    try:
        cancelled = cancel_pending_user(db, user=target, reason=payload.reason, cancelled_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, cancelled)


@router.post("/{user_id}/deactivation/request", response_model=UserOut)
def request_user_deactivation(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("users:manage")),
) -> UserOut:
    enforce_same_organization(organization_id, user, db)
    target = _get_org_user_or_404(db, organization_id, user_id)
    try:
        updated = request_deactivation(db, user=target, reason=payload.reason, requested_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{user_id}/deactivation/approve", response_model=UserOut)
def approve_user_deactivation(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("users:manage")),
) -> UserOut:
    enforce_same_organization(organization_id, user, db)
    target = _get_org_user_or_404(db, organization_id, user_id)
    try:
        updated = approve_deactivation(db, user=target, approved_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{user_id}/deactivation/reject", response_model=UserOut)
def reject_user_deactivation(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("users:manage")),
) -> UserOut:
    enforce_same_organization(organization_id, user, db)
    target = _get_org_user_or_404(db, organization_id, user_id)
    try:
        updated = reject_deactivation(db, user=target, reason=payload.reason, rejected_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{user_id}/deactivation/cancel", response_model=UserOut)
def cancel_user_deactivation(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("users:manage")),
) -> UserOut:
    enforce_same_organization(organization_id, user, db)
    target = _get_org_user_or_404(db, organization_id, user_id)
    try:
        updated = cancel_deactivation(db, user=target, reason=payload.reason, cancelled_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{user_id}/removal/request", response_model=UserOut)
def request_user_removal(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("users:manage")),
) -> UserOut:
    enforce_same_organization(organization_id, user, db)
    target = _get_org_user_or_404(db, organization_id, user_id)
    try:
        updated = request_removal(db, user=target, reason=payload.reason, requested_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{user_id}/removal/approve", response_model=UserOut)
def approve_user_removal(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("users:manage")),
) -> UserOut:
    enforce_same_organization(organization_id, user, db)
    target = _get_org_user_or_404(db, organization_id, user_id)
    try:
        updated = approve_removal(db, user=target, approved_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{user_id}/removal/reject", response_model=UserOut)
def reject_user_removal(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("users:manage")),
) -> UserOut:
    enforce_same_organization(organization_id, user, db)
    target = _get_org_user_or_404(db, organization_id, user_id)
    try:
        updated = reject_removal(db, user=target, reason=payload.reason, rejected_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{user_id}/removal/cancel", response_model=UserOut)
def cancel_user_removal(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: UserReasonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("users:manage")),
) -> UserOut:
    enforce_same_organization(organization_id, user, db)
    target = _get_org_user_or_404(db, organization_id, user_id)
    try:
        updated = cancel_removal(db, user=target, reason=payload.reason, cancelled_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, updated)
