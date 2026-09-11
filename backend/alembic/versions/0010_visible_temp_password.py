"""Persist temporary password in plaintext until account activation

Explicit product decision (user, 2026-09-05): the temporary password for a
newly created user (organization admin bootstrap, or any user added via the
Users page) must stay visible/copyable in the UI for as long as the account
is still pending — not just shown once at creation — so the System Owner or
a client admin can come back to it later and relay it to the person out of
band. This trades a small, explicitly-accepted window of plaintext-password-
at-rest for that usability requirement; the column is wiped the moment the
user activates their account and sets their own password (see
auth_service.activate_pending_user), so the exposure window is bounded to
"before first login" and gated by the same users:manage / organizations:manage
permissions that already control who can see this org's users at all.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-05

"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN temporary_password_plaintext VARCHAR(255)")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS temporary_password_plaintext")
