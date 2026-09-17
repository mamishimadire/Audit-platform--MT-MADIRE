"""Let a user clear an item out of their own notification bell.

Pending-approval items are computed live from real table state (a
pending rule, an exception assigned to you, ...), not stored as
notification rows themselves — there was previously no way to mark one
as seen/cleared, so the bell only ever grew. notification_dismissals
records that a specific user has cleared a specific (category,
entity_id) pair; the "current" list filters those out, and a "history"
view can show them back.

Revision ID: 0068
Revises: 0067
Create Date: 2026-09-17
"""
from alembic import op


revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE notification_dismissals (
            dismissal_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id          UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            category         VARCHAR(50) NOT NULL,
            entity_id        UUID NOT NULL,
            label            TEXT NOT NULL,
            detail           TEXT,
            link_path        VARCHAR(255) NOT NULL,
            notification_at  TIMESTAMPTZ NOT NULL,
            dismissed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (user_id, category, entity_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_notification_dismissals_user_id ON notification_dismissals (user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notification_dismissals")
