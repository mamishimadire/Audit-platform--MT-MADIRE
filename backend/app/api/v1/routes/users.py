import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.rbac import User
from app.schemas.user import UserCreate, UserOut
from app.services.auth_service import get_role_names_bulk, get_user_role_names
from app.services.user_service import create_user_in_organization

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
    stmt = select(User).where(User.organization_id == organization_id)
    users = list(db.scalars(stmt))
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
