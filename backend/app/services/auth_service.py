import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.organization import Organization
from app.models.rbac import Permission, Role, RolePermission, User, UserRole
from app.services.audit_log_service import log_action

# A password older than this is expired outright — must_change_password
# (see password_expiry_status) turns true and ProtectedRoute forces the
# user to /profile before they can reach anything else. REMINDER_DAYS
# before that, password_expiry_status starts reporting a day count so the
# app can show a "change it soon" nudge without yet blocking anything.
PASSWORD_ROTATION_DAYS = 30
PASSWORD_REMINDER_DAYS = 7


def authenticate_user(db: Session, *, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(password, user.password_hash):
        return None
    if user.status != "active":
        return None
    return user


def activate_pending_user(db: Session, *, email: str, temporary_password: str, new_password: str) -> User | None:
    """
    First-login flow for accounts created via organization onboarding
    (status='pending', server-generated temporary password). Verifies the
    temporary password, sets a new one the user chose, and flips the account
    to 'active'. Returns None on any mismatch — never reveals which part failed.
    """
    user = db.scalar(select(User).where(User.email == email, User.status == "pending"))
    if user is None or not verify_password(temporary_password, user.password_hash):
        return None
    user.password_hash = hash_password(new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    user.status = "active"
    user.temporary_password_plaintext = None

    # An organization stays 'onboarding' until someone from it actually
    # shows up — this is that moment, for whichever of its users gets
    # there first. Nothing else in the system ever makes this transition,
    # so without it every organization would sit on 'onboarding' forever.
    if user.organization_id is not None:
        organization = db.get(Organization, user.organization_id)
        if organization is not None and organization.status == "onboarding":
            organization.status = "active"
            log_action(
                db,
                action=f"Organization '{organization.organization_name}' is now active — {user.email} activated their account",
                organization_id=organization.organization_id,
                user_id=user.user_id,
                entity_type="organizations",
                entity_id=organization.organization_id,
                new_value={"status": "active"},
            )

    db.commit()
    db.refresh(user)
    return user


def change_password(db: Session, *, user: User, current_password: str, new_password: str) -> User:
    """Self-service rotation from the profile page, or in response to the
    30-day forced-change redirect. Requires the CURRENT password — a
    logged-in session alone is never enough to walk away with a changed
    password, since anyone briefly at an unlocked, still-logged-in screen
    would otherwise be able to lock the real owner out."""
    if not verify_password(current_password, user.password_hash):
        raise ValueError("Current password is incorrect.")
    if verify_password(new_password, user.password_hash):
        raise ValueError("New password must be different from your current password.")
    user.password_hash = hash_password(new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def password_expiry_status(user: User) -> tuple[bool, int | None]:
    """(must_change_password, reminder_days_remaining). reminder_days_
    remaining is only set once the password is within PASSWORD_REMINDER_DAYS
    of expiring (and is 0, not negative, once it's already expired) — None
    otherwise, so the UI shows nothing until there's actually something to
    say. password_changed_at is only null for a still-pending account that
    has never logged in; nothing to report yet, since activate_pending_user
    always sets it going forward."""
    if user.password_changed_at is None:
        return False, None
    age = datetime.now(timezone.utc) - user.password_changed_at
    if age >= timedelta(days=PASSWORD_ROTATION_DAYS):
        return True, 0
    days_remaining = PASSWORD_ROTATION_DAYS - age.days
    if days_remaining <= PASSWORD_REMINDER_DAYS:
        return False, days_remaining
    return False, None


def get_user_role_names(db: Session, user_id: uuid.UUID) -> list[str]:
    rows = db.scalars(
        select(Role.role_name).join(UserRole, UserRole.role_id == Role.role_id).where(UserRole.user_id == user_id)
    )
    return list(rows)


def get_role_names_bulk(db: Session, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    """Batched equivalent of calling get_user_role_names once per user —
    one query total instead of one per user, so an N-user list page
    doesn't pay N round trips just for the roles column."""
    if not user_ids:
        return {}
    rows = db.execute(
        select(UserRole.user_id, Role.role_name)
        .join(Role, Role.role_id == UserRole.role_id)
        .where(UserRole.user_id.in_(user_ids))
    )
    result: dict[uuid.UUID, list[str]] = {uid: [] for uid in user_ids}
    for user_id, role_name in rows:
        result[user_id].append(role_name)
    return result


def get_user_permission_names(db: Session, user_id: uuid.UUID) -> set[str]:
    rows = db.scalars(
        select(Permission.permission_name)
        .join(RolePermission, RolePermission.permission_id == Permission.permission_id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
    )
    return set(rows)
