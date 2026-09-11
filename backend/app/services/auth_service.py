import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.rbac import Permission, Role, RolePermission, User, UserRole


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
    user.status = "active"
    user.temporary_password_plaintext = None
    db.commit()
    db.refresh(user)
    return user


def get_user_role_names(db: Session, user_id: uuid.UUID) -> list[str]:
    rows = db.scalars(
        select(Role.role_name).join(UserRole, UserRole.role_id == Role.role_id).where(UserRole.user_id == user_id)
    )
    return list(rows)


def get_user_permission_names(db: Session, user_id: uuid.UUID) -> set[str]:
    rows = db.scalars(
        select(Permission.permission_name)
        .join(RolePermission, RolePermission.permission_id == Permission.permission_id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
    )
    return set(rows)
