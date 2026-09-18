import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.rbac import Permission, Role, RolePermission, User, UserOrganizationScope, UserRole
from app.schemas.user import PlatformUserCreate, UserCreate
from app.services.audit_log_service import log_action


def create_user_in_organization(
    db: Session, *, organization_id: uuid.UUID, payload: UserCreate, created_by_user_id: uuid.UUID
) -> User:
    # The system always generates the temporary password — nobody has to
    # invent one, and it stays visible on this user's row (see
    # temporary_password_plaintext) until they activate their own account.
    temporary_password = secrets.token_urlsafe(18)
    user = User(
        organization_id=organization_id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        password_hash=hash_password(temporary_password),
        # Not usable yet — a different authorized user has to approve
        # this account first (see approve_pending_user). The person who
        # just created it can't also be the one who signs off on it.
        status="pending_approval",
        temporary_password_plaintext=temporary_password,
        created_by=created_by_user_id,
    )
    db.add(user)
    db.flush()

    for role_name in payload.role_names:
        role = db.scalar(select(Role).where(Role.role_name == role_name))
        if role is None:
            raise ValueError(f"Unknown role: {role_name}")
        if role.role_scope != "client":
            # A client user (organization_id is set, as it is here) can never
            # hold a platform-scoped role — that boundary is what keeps a
            # client administrator from ever becoming a platform administrator.
            raise ValueError(f"Role '{role_name}' is platform-scoped and cannot be assigned to a client user")
        db.add(UserRole(user_id=user.user_id, role_id=role.role_id))

    log_action(
        db,
        action=f"Created user '{user.email}'",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="users",
        entity_id=user.user_id,
        new_value={"email": user.email, "role_names": payload.role_names},
    )

    db.commit()
    db.refresh(user)
    return user


def create_platform_user(db: Session, *, payload: PlatformUserCreate, created_by_user_id: uuid.UUID) -> User:
    """
    Adds one of MT AUDIT's own internal staff — the mirror of
    create_user_in_organization for the platform side. organization_id is
    always NULL, and the reverse boundary applies: only platform-scoped
    roles may be assigned, never a client-scoped one.
    """
    temporary_password = secrets.token_urlsafe(18)
    user = User(
        organization_id=None,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        password_hash=hash_password(temporary_password),
        status="pending_approval",
        temporary_password_plaintext=temporary_password,
        created_by=created_by_user_id,
    )
    db.add(user)
    db.flush()

    for role_name in payload.role_names:
        role = db.scalar(select(Role).where(Role.role_name == role_name))
        if role is None:
            raise ValueError(f"Unknown role: {role_name}")
        if role.role_scope != "platform":
            raise ValueError(f"Role '{role_name}' is client-scoped and cannot be assigned to an internal user")
        db.add(UserRole(user_id=user.user_id, role_id=role.role_id))

    log_action(
        db,
        action=f"Created internal user '{user.email}'",
        organization_id=None,
        user_id=created_by_user_id,
        entity_type="users",
        entity_id=user.user_id,
        new_value={"email": user.email, "role_names": payload.role_names},
    )

    db.commit()
    db.refresh(user)
    return user


def list_platform_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.organization_id.is_(None))))


def list_users_with_permission_for_organization(
    db: Session, *, organization_id: uuid.UUID, permission_name: str
) -> list[User]:
    """
    Every real person currently eligible to act as checker for a
    dual-control action gated by `permission_name` within this
    organization — a client user needs the permission directly; a platform
    user needs it AND either the Platform Super Admin break-glass role or
    an explicit user_organization_scope row for this org (the same rule
    tenant_scope_service already applies to reads). Lets a request dialog
    say who the approval is actually going to, instead of a vague
    "a different authorized user" placeholder.
    """
    base = (
        select(User)
        .join(UserRole, UserRole.user_id == User.user_id)
        .join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .join(Permission, Permission.permission_id == RolePermission.permission_id)
        .where(Permission.permission_name == permission_name, User.status == "active")
        .distinct()
    )
    client_users = db.scalars(base.where(User.organization_id == organization_id)).all()

    super_admin_ids = (
        select(UserRole.user_id).join(Role, Role.role_id == UserRole.role_id).where(Role.role_name == "Platform Super Admin")
    )
    scoped_ids = select(UserOrganizationScope.user_id).where(UserOrganizationScope.organization_id == organization_id)
    platform_users = db.scalars(
        base.where(User.organization_id.is_(None), or_(User.user_id.in_(super_admin_ids), User.user_id.in_(scoped_ids)))
    ).all()

    seen: dict[uuid.UUID, User] = {}
    for u in (*client_users, *platform_users):
        seen[u.user_id] = u
    return list(seen.values())


def approve_pending_user(db: Session, *, user: User, approved_by_user_id: uuid.UUID) -> User:
    """Maker-checker, identity-based — same pattern as test_rule_service.
    approve_rule. Whoever created this account cannot also be the one who
    approves it, even holding the exact same users:manage/organizations:
    manage permission."""
    if user.created_by is not None and user.created_by == approved_by_user_id:
        raise ValueError("You added this user yourself — a different authorized user must approve them.")
    if user.status != "pending_approval":
        raise ValueError(f"This user is '{user.status}', not pending approval.")

    user.status = "pending"
    user.approved_by = approved_by_user_id
    user.approved_at = datetime.now(timezone.utc)
    log_action(
        db,
        action=f"Approved new user '{user.email}'",
        organization_id=user.organization_id,
        user_id=approved_by_user_id,
        entity_type="users",
        entity_id=user.user_id,
        new_value={"status": "pending"},
    )
    db.commit()
    db.refresh(user)
    return user


def reject_pending_user(db: Session, *, user: User, reason: str, rejected_by_user_id: uuid.UUID) -> User:
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reject a new user.")
    if user.status != "pending_approval":
        raise ValueError(f"This user is '{user.status}', not pending approval.")

    user.status = "rejected"
    log_action(
        db,
        action=f"Rejected new user '{user.email}': {reason}",
        organization_id=user.organization_id,
        user_id=rejected_by_user_id,
        entity_type="users",
        entity_id=user.user_id,
        new_value={"status": "rejected", "reason": reason},
    )
    db.commit()
    db.refresh(user)
    return user
