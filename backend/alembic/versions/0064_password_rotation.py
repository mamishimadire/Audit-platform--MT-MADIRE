"""Track when each user's password was last changed, for the 30-day
rotation policy (self-service change requires the current password;
/auth/me reports whether a change is due or coming up soon; the app
forces a redirect to the profile page once it's overdue).

Existing users are backfilled to their account's created_at — the exact
historical change date isn't known, but this is the same conservative
assumption activation already makes (a freshly created account's
password is "as old as the account" until proven otherwise), and it's
what starts their 30-day clock instead of leaving it null forever.

Revision ID: 0064
Revises: 0063
Create Date: 2026-09-17
"""
from alembic import op


revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN password_changed_at TIMESTAMPTZ")
    op.execute("UPDATE users SET password_changed_at = created_at WHERE password_changed_at IS NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS password_changed_at")
