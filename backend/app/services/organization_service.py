import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.industry import Industry, OrganizationIndustry
from app.models.organization import Organization
from app.models.rbac import Role, User, UserRole
from app.schemas.organization import OrganizationCreate
from app.services.audit_log_service import log_action
from app.services.risk_auto_seed_service import auto_seed_risks_for_organization, validate_industry_ids

BOOTSTRAP_ADMIN_ROLE = "Client Organisation Admin"


def create_organization_with_admin(
    db: Session, *, payload: OrganizationCreate, created_by_user_id: uuid.UUID
) -> tuple[Organization, User, str]:
    """
    Stands up a new client tenant and its first Client Administrator, mirroring
    the product spec's "owner adds a client" flow. There is no email/invite
    service yet (that's later scope per the build order), so a temporary
    password is generated, handed back to the caller, and also persisted in
    plaintext on the user row so it stays visible/copyable in the Users list
    until the admin activates their account (a deliberate product decision —
    see migration 0010). The account is created with status='pending' until
    first login triggers a password change, at which point the plaintext
    copy is wiped.

    The selected industries auto-create this organization's starting risk
    register from the risk library (see risk_auto_seed_service) — no blank
    Risks page, less manual effort, and each risk stays editable afterward.
    """
    validate_industry_ids(db, industry_ids=payload.industry_ids)

    industry_names: list[str] = []
    if payload.industry_ids:
        industry_names = list(
            db.scalars(select(Industry.industry_name).where(Industry.industry_id.in_(payload.industry_ids)))
        )

    organization = Organization(
        organization_name=payload.organization_name,
        trading_name=payload.trading_name,
        registration_number=payload.registration_number,
        # Legacy single free-text column, kept populated for anything still
        # reading it directly — the real source of truth is
        # organization_industries (see OrganizationOut.industries).
        industry=", ".join(sorted(industry_names)) if industry_names else None,
        country=payload.country,
        status="onboarding",
        created_by=created_by_user_id,
    )
    db.add(organization)
    db.flush()  # populate organization.organization_id

    for industry_id in payload.industry_ids:
        db.add(OrganizationIndustry(organization_id=organization.organization_id, industry_id=industry_id))

    temporary_password = secrets.token_urlsafe(18)
    admin_user = User(
        organization_id=organization.organization_id,
        first_name=payload.primary_admin_first_name,
        last_name=payload.primary_admin_last_name,
        email=payload.primary_admin_email,
        password_hash=hash_password(temporary_password),
        status="pending",
        # Stays visible in the Users list until this admin activates their
        # account — see migration 0010.
        temporary_password_plaintext=temporary_password,
    )
    db.add(admin_user)
    db.flush()

    role = db.scalar(select(Role).where(Role.role_name == BOOTSTRAP_ADMIN_ROLE))
    if role is not None:  # defensive: migrations always seed this, but never hard-fail onboarding on it
        db.add(UserRole(user_id=admin_user.user_id, role_id=role.role_id))

    auto_seed_risks_for_organization(
        db, organization_id=organization.organization_id, industry_ids=payload.industry_ids, created_by_user_id=created_by_user_id
    )

    log_action(
        db,
        action=f"Created organization '{organization.organization_name}'",
        organization_id=organization.organization_id,
        user_id=created_by_user_id,
        entity_type="organizations",
        entity_id=organization.organization_id,
        new_value={"organization_name": organization.organization_name, "status": organization.status},
    )

    db.commit()
    db.refresh(organization)
    db.refresh(admin_user)
    return organization, admin_user, temporary_password
