import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, require_roles
from app.db.session import get_db
from app.models.organization import Organization
from app.models.rbac import User, UserOrganizationScope
from app.schemas.organization import OrganizationOut
from app.schemas.scope import ScopeGrant, ScopeOut
from app.services.tenant_scope_service import grant_organization_scope, revoke_organization_scope

router = APIRouter(prefix="/organizations/{organization_id}/scope-assignments", tags=["scope-assignments"])

# Granting a platform user access to a client is itself a platform-admin-level
# action — deliberately not opened up to every platform role, since it's the
# one lever that controls "who can see this client at all."
_PLATFORM_ADMIN_ROLES = ("Platform Super Admin", "Platform Admin")

# A separate top-level router (no {organization_id} in the path) for the
# engagement-assignment screen, which — unlike the general organizations
# list — deliberately DOES need to see every client so an admin can assign
# any of them. Scoped to the same two roles that can grant/revoke access at
# all, so it doesn't widen who can see the full client roster.
admin_router = APIRouter(prefix="/scope-assignments", tags=["scope-assignments"])


@admin_router.get("/organizations", response_model=list[OrganizationOut])
def list_all_organizations_for_assignment(
    db: Session = Depends(get_db), _user: User = Depends(require_roles(*_PLATFORM_ADMIN_ROLES))
) -> list[Organization]:
    return list(db.scalars(select(Organization).order_by(Organization.organization_name)))


@router.post("", response_model=ScopeOut, status_code=status.HTTP_201_CREATED)
def grant(
    organization_id: uuid.UUID,
    payload: ScopeGrant,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_PLATFORM_ADMIN_ROLES)),
) -> UserOrganizationScope:
    # A Platform Admin (unlike Platform Super Admin) is itself org-scoped —
    # without this check, a Platform Admin scoped only to Client A could
    # grant themselves or anyone else access to Client B by simply changing
    # the organization_id in the URL. Granting scope to an org requires
    # already having scope to that org (Platform Super Admin always passes).
    enforce_same_organization(organization_id, user, db)
    target_user = db.get(User, payload.user_id)
    if target_user is None or target_user.organization_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Scope can only be granted to a platform user (no organization_id)"
        )
    if target_user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Scope can only be granted to an active user — this one is still awaiting approval/activation, or is deactivated/removed.",
        )
    return grant_organization_scope(db, user_id=payload.user_id, organization_id=organization_id, granted_by_user_id=user.user_id)


@router.get("", response_model=list[ScopeOut])
def list_grants(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_roles(*_PLATFORM_ADMIN_ROLES))
) -> list[UserOrganizationScope]:
    enforce_same_organization(organization_id, user, db)
    return list(db.scalars(select(UserOrganizationScope).where(UserOrganizationScope.organization_id == organization_id)))


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_PLATFORM_ADMIN_ROLES)),
) -> None:
    enforce_same_organization(organization_id, user, db)
    revoke_organization_scope(db, user_id=user_id, organization_id=organization_id)
