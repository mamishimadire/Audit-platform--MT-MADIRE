"""Evidence requests and the auditor/client comment thread on exceptions.

Two new, independent tables:

- evidence_requests: a named, tracked ask for one specific document
  ("AD deprovisioning ticket for JSMITH"), with a due date and its own
  awaiting/received lifecycle, separate from the exception's own status.
  The uploaded file is stored inline (file_data BYTEA) — no object
  storage exists yet, and these are individual documents, not something
  that needs a CDN.
- exception_comments: a plain chronological log of the auditor/client
  discussion on one exception. No editing or deleting — an audit trail
  of who said what, when, is the point.

Revision ID: 0065
Revises: 0064
Create Date: 2026-09-17
"""
from alembic import op


revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE evidence_requests (
            request_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            exception_id   UUID NOT NULL REFERENCES exceptions(exception_id) ON DELETE CASCADE,
            description    TEXT NOT NULL,
            due_date       DATE,
            status         VARCHAR(20) NOT NULL DEFAULT 'awaiting' CHECK (status IN ('awaiting', 'received')),
            requested_by   UUID REFERENCES users(user_id) ON DELETE SET NULL,
            requested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            file_name      VARCHAR(255),
            content_type   VARCHAR(100),
            file_data      BYTEA,
            uploaded_by    UUID REFERENCES users(user_id) ON DELETE SET NULL,
            uploaded_at    TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX idx_evidence_requests_exception_id ON evidence_requests (exception_id)")

    op.execute(
        """
        CREATE TABLE exception_comments (
            comment_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            exception_id   UUID NOT NULL REFERENCES exceptions(exception_id) ON DELETE CASCADE,
            author_id      UUID REFERENCES users(user_id) ON DELETE SET NULL,
            body           TEXT NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_exception_comments_exception_id ON exception_comments (exception_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS exception_comments")
    op.execute("DROP TABLE IF EXISTS evidence_requests")
