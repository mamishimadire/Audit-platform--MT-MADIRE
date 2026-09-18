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
    """A different person from whoever added this user — the adder
    withdraws their own submission via cancel_pending_user instead,
    never this."""
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reject a new user.")
    if user.status != "pending_approval":
        raise ValueError(f"This user is '{user.status}', not pending approval.")
    if user.created_by is not None and user.created_by == rejected_by_user_id:
        raise ValueError("You added this user yourself — a different authorized user must reject them, or cancel your own submission instead.")

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


def cancel_pending_user(db: Session, *, user: User, reason: str, cancelled_by_user_id: uuid.UUID) -> User:
    """The adder withdrawing their OWN still-pending new-user submission —
    only they may do this, no one else."""
    if not reason or not reason.strip():
        raise ValueError("A reason is required to cancel a new user submission.")
    if user.status != "pending_approval":
        raise ValueError(f"This user is '{user.status}', not pending approval.")
    if user.created_by != cancelled_by_user_id:
        raise ValueError("Only the person who added this user can cancel it.")

    user.status = "rejected"
    log_action(
        db,
        action=f"Cancelled own new-user submission '{user.email}': {reason}",
        organization_id=user.organization_id,
        user_id=cancelled_by_user_id,
        entity_type="users",
        entity_id=user.user_id,
        new_value={"status": "rejected", "reason": reason},
    )
    db.commit()
    db.refresh(user)
    return user


def request_deactivation(db: Session, *, user: User, reason: str, requested_by_user_id: uuid.UUID) -> User:
    """Same dual-control principle as device_service.request_revocation —
    the person who wants a colleague's access switched off is never the
    one who gets to make that happen unilaterally."""
    if user.status != "active":
        raise ValueError(f"Cannot request deactivation — user is '{user.status}', not active.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to request deactivation.")

    user.status = "pending_deactivation"
    user.deactivation_requested_by = requested_by_user_id
    user.deactivation_requested_at = datetime.now(timezone.utc)
    user.deactivation_reason = reason
    log_action(
        db,
        action=f"Requested deactivation of user '{user.email}': {reason}",
        organization_id=user.organization_id,
        user_id=requested_by_user_id,
        entity_type="users",
        entity_id=user.user_id,
        new_value={"status": "pending_deactivation", "reason": reason},
    )
    db.commit()
    db.refresh(user)
    return user


def approve_deactivation(db: Session, *, user: User, approved_by_user_id: uuid.UUID) -> User:
    if user.status != "pending_deactivation":
        raise ValueError(f"Cannot approve — user is '{user.status}', not pending deactivation.")
    if user.deactivation_requested_by is not None and user.deactivation_requested_by == approved_by_user_id:
        raise ValueError("You requested this deactivation yourself — a different authorized user must approve it.")

    user.status = "inactive"
    log_action(
        db,
        action=f"Approved deactivation of user '{user.email}'",
        organization_id=user.organization_id,
        user_id=approved_by_user_id,
        entity_type="users",
        entity_id=user.user_id,
        new_value={"status": "inactive"},
    )
    db.commit()
    db.refresh(user)
    return user


def reject_deactivation(db: Session, *, user: User, reason: str, rejected_by_user_id: uuid.UUID) -> User:
    """A different person from whoever requested this deactivation — the
    requester withdraws their own request via cancel_deactivation
    instead, never this."""
    if user.status != "pending_deactivation":
        raise ValueError(f"Cannot reject — user is '{user.status}', not pending deactivation.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reject a deactivation request.")
    if user.deactivation_requested_by is not None and user.deactivation_requested_by == rejected_by_user_id:
        raise ValueError("You requested this deactivation yourself — a different authorized user must reject it, or cancel your own request instead.")

    user.status = "active"
    user.deactivation_requested_by = None
    user.deactivation_reason = None
    log_action(
        db,
        action=f"Rejected deactivation of user '{user.email}': {reason}",
        organization_id=user.organization_id,
        user_id=rejected_by_user_id,
        entity_type="users",
        entity_id=user.user_id,
        new_value={"status": "active", "reason": reason},
    )
    db.commit()
    db.refresh(user)
    return user


def cancel_deactivation(db: Session, *, user: User, reason: str, cancelled_by_user_id: uuid.UUID) -> User:
    """The requester withdrawing their OWN still-pending deactivation
    request — only they may do this, no one else."""
    if user.status != "pending_deactivation":
        raise ValueError(f"Cannot cancel — user is '{user.status}', not pending deactivation.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to cancel a deactivation request.")
    if user.deactivation_requested_by != cancelled_by_user_id:
        raise ValueError("Only the person who requested this deactivation can cancel it.")

    user.status = "active"
    user.deactivation_requested_by = None
    user.deactivation_reason = None
    log_action(
        db,
        action=f"Cancelled own deactivation request for user '{user.email}': {reason}",
        organization_id=user.organization_id,
        user_id=cancelled_by_user_id,
        entity_type="users",
        entity_id=user.user_id,
        new_value={"status": "active", "reason": reason},
    )
    db.commit()
    db.refresh(user)
    return user


_REMOVABLE_STATUSES = ("active", "inactive", "pending", "locked")


def request_removal(db: Session, *, user: User, reason: str, requested_by_user_id: uuid.UUID) -> User:
    """Same dual-control principle as device_service.request_deletion.
    'removed' is a soft delete — the row and everything it ever did in
    the audit log stay; it just can never log in again."""
    if user.status not in _REMOVABLE_STATUSES:
        raise ValueError(f"Cannot request removal — user is '{user.status}'.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to request removal.")

    user.removal_prior_status = user.status
    user.status = "pending_removal"
    user.removal_requested_by = requested_by_user_id
    user.removal_requested_at = datetime.now(timezone.utc)
    user.removal_reason = reason
    log_action(
        db,
        action=f"Requested removal of user '{user.email}': {reason}",
        organization_id=user.organization_id,
        user_id=requested_by_user_id,
        entity_type="users",
        entity_id=user.user_id,
        new_value={"status": "pending_removal", "reason": reason},
    )
    db.commit()
    db.refresh(user)
    return user


def approve_removal(db: Session, *, user: User, approved_by_user_id: uuid.UUID) -> User:
    if user.status != "pending_removal":
        raise ValueError(f"Cannot approve — user is '{user.status}', not pending removal.")
    if user.removal_requested_by is not None and user.removal_requested_by == approved_by_user_id:
        raise ValueError("You requested this removal yourself — a different authorized user must approve it.")

    user.status = "removed"
    user.removal_prior_status = None
    log_action(
        db,
        action=f"Approved removal of user '{user.email}'",
        organization_id=user.organization_id,
        user_id=approved_by_user_id,
        entity_type="users",
        entity_id=user.user_id,
        new_value={"status": "removed"},
    )
    db.commit()
    db.refresh(user)
    return user


def reject_removal(db: Session, *, user: User, reason: str, rejected_by_user_id: uuid.UUID) -> User:
    """A different person from whoever requested this removal — the
    requester withdraws their own request via cancel_removal instead,
    never this."""
    if user.status != "pending_removal":
        raise ValueError(f"Cannot reject — user is '{user.status}', not pending removal.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reject a removal request.")
    if user.removal_requested_by is not None and user.removal_requested_by == rejected_by_user_id:
        raise ValueError("You requested this removal yourself — a different authorized user must reject it, or cancel your own request instead.")

    user.status = user.removal_prior_status or "active"
    user.removal_prior_status = None
    user.removal_requested_by = None
    user.removal_reason = None
    log_action(
        db,
        action=f"Rejected removal of user '{user.email}': {reason}",
        organization_id=user.organization_id,
        user_id=rejected_by_user_id,
        entity_type="users",
        entity_id=user.user_id,
        new_value={"status": user.status, "reason": reason},
    )
    db.commit()
    db.refresh(user)
    return user


def cancel_removal(db: Session, *, user: User, reason: str, cancelled_by_user_id: uuid.UUID) -> User:
    """The requester withdrawing their OWN still-pending removal
    request — only they may do this, no one else."""
    if user.status != "pending_removal":
        raise ValueError(f"Cannot cancel — user is '{user.status}', not pending removal.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to cancel a removal request.")
    if user.removal_requested_by != cancelled_by_user_id:
        raise ValueError("Only the person who requested this removal can cancel it.")

    user.status = user.removal_prior_status or "active"
    user.removal_prior_status = None
    user.removal_requested_by = None
    user.removal_reason = None
    log_action(
        db,
        action=f"Cancelled own removal request for user '{user.email}': {reason}",
        organization_id=user.organization_id,
        user_id=cancelled_by_user_id,
        entity_type="users",
        entity_id=user.user_id,
        new_value={"status": user.status, "reason": reason},
    )
    db.commit()
    db.refresh(user)
    return user
