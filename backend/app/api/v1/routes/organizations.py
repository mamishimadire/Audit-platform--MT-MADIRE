import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.organization import Organization
from app.models.rbac import User
from app.schemas.organization import OrganizationCreate, OrganizationCreatedOut, OrganizationOut
from app.services.industry_service import get_industry_names_bulk, get_industry_names_for_organization
from app.services.organization_service import create_organization_with_admin
from app.services.tenant_scope_service import PLATFORM_SUPER_ADMIN_ROLE, scoped_organization_ids, user_has_role

router = APIRouter(prefix="/organizations", tags=["organizations"])
logger = logging.getLogger("audit_platform.onboarding")


def _to_out(db: Session, organization: Organization) -> OrganizationOut:
    out = OrganizationOut.model_validate(organization)
    return out.model_copy(
        update={"industries": get_industry_names_for_organization(db, organization_id=organization.organization_id)}
    )


@router.post("", response_model=OrganizationCreatedOut, status_code=status.HTTP_201_CREATED)
def create_organization(
    payload: OrganizationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("organizations:manage")),
) -> OrganizationCreatedOut:
    try:
        organization, admin_user, temporary_password = create_organization_with_admin(
            db, payload=payload, created_by_user_id=user.user_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    # No email/invite delivery service exists yet (that's later scope per the
    # build order), so the temporary password is handed back here to the
    # System Owner who just created this client, and also stays visible on
    # the organization's Users page until the admin activates their account
    # (migration 0010) — also mirrored to the server log as a fallback.
    logger.info(
        "Client '%s' onboarded. First login for %s: temporary password = %s (must be changed on first login)",
        organization.organization_name,
        admin_user.email,
        temporary_password,
    )
    out = _to_out(db, organization)
    return OrganizationCreatedOut(
        **out.model_dump(),
        primary_admin_email=admin_user.email,
        temporary_password=temporary_password,
    )


@router.get("", response_model=list[OrganizationOut])
def list_organizations(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[OrganizationOut]:
    if user.organization_id is not None:
        # A client user sees only their own organization.
        stmt = select(Organization).where(Organization.organization_id == user.organization_id)
    elif user_has_role(db, user.user_id, PLATFORM_SUPER_ADMIN_ROLE):
        stmt = select(Organization)
    else:
        # A platform user without Platform Super Admin sees only the
        # organizations they've been explicitly granted — never "all clients"
        # by default, however many thousands exist.
        org_ids = scoped_organization_ids(db, user.user_id)
        stmt = select(Organization).where(Organization.organization_id.in_(org_ids))
    orgs = list(db.scalars(stmt))
    industries_by_org = get_industry_names_bulk(db, [o.organization_id for o in orgs])
    return [
        OrganizationOut.model_validate(org).model_copy(update={"industries": industries_by_org.get(org.organization_id, [])})
        for org in orgs
    ]


@router.get("/{organization_id}", response_model=OrganizationOut)
def get_organization(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> OrganizationOut:
    enforce_same_organization(organization_id, user, db)
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return _to_out(db, organization)
