import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.rbac import Role, User, UserOrganizationScope, UserRole
from app.services.audit_log_service import log_action

PLATFORM_SUPER_ADMIN_ROLE = "Platform Super Admin"


def user_has_role(db: Session, user_id: uuid.UUID, role_name: str) -> bool:
    return (
        db.scalar(
            select(UserRole)
            .join(Role, Role.role_id == UserRole.role_id)
            .where(UserRole.user_id == user_id, Role.role_name == role_name)
        )
        is not None
    )


def has_organization_scope(db: Session, user_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(UserOrganizationScope).where(
                UserOrganizationScope.user_id == user_id, UserOrganizationScope.organization_id == organization_id
            )
        )
        is not None
    )


def scoped_organization_ids(db: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.scalars(select(UserOrganizationScope.organization_id).where(UserOrganizationScope.user_id == user_id)))


def grant_organization_scope(
    db: Session, *, user_id: uuid.UUID, organization_id: uuid.UUID, granted_by_user_id: uuid.UUID
) -> UserOrganizationScope:
    scope = UserOrganizationScope(user_id=user_id, organization_id=organization_id, granted_by=granted_by_user_id)
    db.add(scope)
    db.flush()
    log_action(
        db,
        action="Granted platform user access to organization",
        organization_id=organization_id,
        user_id=granted_by_user_id,
        entity_type="user_organization_scope",
        entity_id=scope.scope_id,
        new_value={"user_id": str(user_id)},
    )
    db.commit()
    db.refresh(scope)
    return scope


def revoke_organization_scope(db: Session, *, user_id: uuid.UUID, organization_id: uuid.UUID) -> None:
    db.query(UserOrganizationScope).filter(
        UserOrganizationScope.user_id == user_id, UserOrganizationScope.organization_id == organization_id
    ).delete()
    db.commit()


def can_access_organization(db: Session, *, user: User, organization_id: uuid.UUID) -> bool:
    """
    A client user (organization_id set) may only access their own org.
    A platform user (organization_id IS NULL) needs either the global
    Platform Super Admin role, or an explicit scope grant for that org.
    """
    if user.organization_id is not None:
        return user.organization_id == organization_id
    if user_has_role(db, user.user_id, PLATFORM_SUPER_ADMIN_ROLE):
        return True
    return has_organization_scope(db, user.user_id, organization_id)
