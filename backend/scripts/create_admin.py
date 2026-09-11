"""
One-time bootstrap: creates the first Platform Super Admin so someone can
log in at all. Reads credentials from environment variables — never
hard-coded — and is idempotent (safe to re-run; won't overwrite an
existing user's password).

Usage (from backend/, with .env configured and migrations applied):
    ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='a-strong-password' \
    ADMIN_FIRST_NAME=Ada ADMIN_LAST_NAME=Lovelace \
    python -m scripts.create_admin
"""
import os
import sys

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.rbac import Role, User, UserRole  # noqa: E402


def main() -> None:
    email = os.environ.get("ADMIN_EMAIL")
    password = os.environ.get("ADMIN_PASSWORD")
    first_name = os.environ.get("ADMIN_FIRST_NAME", "System")
    last_name = os.environ.get("ADMIN_LAST_NAME", "Administrator")

    if not email or not password:
        print("ADMIN_EMAIL and ADMIN_PASSWORD environment variables are required.", file=sys.stderr)
        sys.exit(1)
    if len(password) < 12:
        print("ADMIN_PASSWORD must be at least 12 characters.", file=sys.stderr)
        sys.exit(1)

    db = SessionLocal()
    try:
        role = db.scalar(select(Role).where(Role.role_name == "Platform Super Admin"))
        if role is None:
            print("Role 'Platform Super Admin' not found — run `alembic upgrade head` first.", file=sys.stderr)
            sys.exit(1)

        existing = db.scalar(select(User).where(User.email == email))
        if existing is not None:
            print(f"User {email} already exists — leaving password untouched.")
            user = existing
        else:
            user = User(
                organization_id=None,  # platform-level user, not scoped to any client tenant
                first_name=first_name,
                last_name=last_name,
                email=email,
                password_hash=hash_password(password),
                status="active",
            )
            db.add(user)
            db.flush()
            print(f"Created platform user {email}.")

        already_has_role = db.scalar(
            select(UserRole).where(UserRole.user_id == user.user_id, UserRole.role_id == role.role_id)
        )
        if already_has_role is None:
            db.add(UserRole(user_id=user.user_id, role_id=role.role_id))
            print("Assigned role 'Platform Super Admin'.")

        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
