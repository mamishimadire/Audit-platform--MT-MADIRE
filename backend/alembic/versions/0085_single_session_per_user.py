"""Enforces one active session per account.

Access tokens were fully stateless (a signed JWT, no server-side record of
which ones are still supposed to be valid) - logging in on a second device
or a second browser tab never affected any token already issued elsewhere,
so the same account could be signed in anywhere, indefinitely, all at once.

`users.current_session_id` names the one session a token must match to
still be honoured. Nullable, and left NULL by this migration for every
existing user on purpose: enforcement only begins the next time each of
them actually logs in (see app.api.deps.get_current_user) - nobody already
signed in gets silently kicked the moment this deploys, and a token issued
before this shipped (no `sid` claim at all) keeps working until then too.

ON DELETE SET NULL, not CASCADE: a user_sessions row is an append-only
audit record (nothing ever deletes one in normal operation) - if it were
ever removed, the account should simply fall back to "no session enforced
yet" rather than the FK blocking the delete outright.

Revision ID: 0085
Revises: 0084
Create Date: 2026-09-22
"""
from alembic import op
from sqlalchemy import text

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            """
            ALTER TABLE users
              ADD COLUMN IF NOT EXISTS current_session_id UUID REFERENCES user_sessions(session_id) ON DELETE SET NULL
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS current_session_id"))
