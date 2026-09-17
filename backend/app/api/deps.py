import uuid

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.data_source import Gateway
from app.models.device import Device
from app.models.rbac import User
from app.services.auth_service import get_user_permission_names, get_user_role_names
from app.services.device_service import authenticate_device
from app.services.gateway_service import authenticate_gateway
from app.services.tenant_scope_service import can_access_organization

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise credentials_error from exc

    user = db.get(User, user_id)
    if user is None or user.status != "active":
        raise credentials_error
    return user


def get_current_user_roles(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[str]:
    return get_user_role_names(db, user.user_id)


def require_permissions(*required: str):
    """
    Dependency factory: 403s unless the current user holds every listed
    permission. Permissions live in the database (roles -> role_permissions
    -> permissions) rather than being hard-coded per-route, per Section 8
    of the build instruction.
    """

    def _check(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        granted = get_user_permission_names(db, user.user_id)
        missing = set(required) - granted
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission(s): {', '.join(sorted(missing))}",
            )
        return user

    return _check


def require_any_permission(*acceptable: str):
    """Dependency factory: 403s unless the current user holds AT LEAST ONE
    of the listed permissions — require_permissions' AND semantics don't
    fit an action two independently-scoped roles can each unlock for their
    own reason (e.g. an internal auditor via audit_framework:manage, a
    client's own admin via exceptions:assign)."""

    def _check(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        granted = get_user_permission_names(db, user.user_id)
        if granted.isdisjoint(acceptable):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of permission(s): {', '.join(sorted(acceptable))}",
            )
        return user

    return _check


def require_roles(*allowed_role_names: str):
    """Coarser-grained alternative to require_permissions for roles like 'Platform Super Admin'."""

    def _check(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        held = set(get_user_role_names(db, user.user_id))
        if held.isdisjoint(allowed_role_names):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of role(s): {', '.join(allowed_role_names)}",
            )
        return user

    return _check


def get_current_gateway(
    x_gateway_id: str = Header(...),
    x_api_key: str = Header(...),
    db: Session = Depends(get_db),
) -> Gateway:
    """
    Authenticates a Gateway process (not a human user) via a device API key,
    per the product spec's "Gateway receives its own device certificate for
    all future communication" — a completely separate credential space from
    user JWTs, since a Gateway is never a User row.
    """
    try:
        gateway_id = uuid.UUID(x_gateway_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid gateway credentials") from exc

    gateway = authenticate_gateway(db, gateway_id=gateway_id, api_key=x_api_key)
    if gateway is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid gateway credentials")
    return gateway


def get_current_device(
    x_device_id: str = Header(...),
    x_api_key: str = Header(...),
    db: Session = Depends(get_db),
) -> Device:
    """Authenticates an enrolled endpoint device — same credential model as get_current_gateway, a device is never a User row."""
    try:
        device_id = uuid.UUID(x_device_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device credentials") from exc

    device = authenticate_device(db, device_id=device_id, api_key=x_api_key)
    if device is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device credentials")
    return device


def enforce_same_organization(target_organization_id: uuid.UUID, user: User, db: Session) -> None:
    """
    Tenant-isolation guard. A client user (organization_id set) may only act
    within their own organization. A platform user (organization_id is NULL)
    may act on a given organization only if they hold 'Platform Super Admin'
    (global) or have an explicit grant in user_organization_scope for that
    specific organization — at thousands-of-clients scale, "platform user"
    must not mean "sees every client." Call this explicitly in any
    route/service that reads or writes an organization-scoped table — do not
    rely on the frontend to filter by org.
    """
    if not can_access_organization(db, user=user, organization_id=target_organization_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this organization")
