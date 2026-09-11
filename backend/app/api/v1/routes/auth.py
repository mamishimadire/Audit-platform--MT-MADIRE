from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import create_access_token
from app.db.session import get_db
from app.models.rbac import User, UserSession
from app.schemas.auth import ActivateAccountRequest, TokenResponse, UserOut
from app.services.audit_log_service import log_action
from app.services.auth_service import activate_pending_user, authenticate_user, get_user_role_names

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> TokenResponse:
    user = authenticate_user(db, email=form.username, password=form.password)
    if user is None:
        # Deliberately identical error for "no such user" and "wrong password" —
        # never reveal which one it was. The log entry still records the
        # attempted email so a brute-force/credential-stuffing pattern is
        # visible even though the user-facing error stays generic; no
        # organization_id/user_id since we never confirm which (if any)
        # real account was targeted.
        log_action(db, action="Failed login attempt", new_value={"email": form.username})
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    db.add(UserSession(user_id=user.user_id, ip_address=request.client.host if request.client else None))
    log_action(db, action="User logged in", organization_id=user.organization_id, user_id=user.user_id)
    db.commit()

    token = create_access_token(user_id=user.user_id, organization_id=user.organization_id)
    return TokenResponse(access_token=token)


@router.post("/activate", response_model=TokenResponse)
def activate_account(payload: ActivateAccountRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """First-login step for a pending account: trade the one-time temporary
    password for a chosen password, then log in immediately."""
    user = activate_pending_user(
        db, email=payload.email, temporary_password=payload.temporary_password, new_password=payload.new_password
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or temporary password")
    log_action(db, action="Account activated", organization_id=user.organization_id, user_id=user.user_id)
    db.commit()
    token = create_access_token(user_id=user.user_id, organization_id=user.organization_id)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
def read_current_user(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UserOut:
    roles = get_user_role_names(db, user.user_id)
    return UserOut.model_validate(user).model_copy(update={"roles": roles})
