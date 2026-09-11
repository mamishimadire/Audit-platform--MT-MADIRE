from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permissions
from app.db.session import get_db
from app.models.rbac import User
from app.schemas.user import PlatformUserCreate, UserOut
from app.services.auth_service import get_user_role_names
from app.services.user_service import create_platform_user, list_platform_users

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
    return [_to_out(db, u) for u in list_platform_users(db)]
