"""Let the client attach more than one file to an evidence request, and
delete one they uploaded by mistake.

evidence_requests previously stored exactly one file inline on the
request row itself (file_name/content_type/file_data/uploaded_by/
uploaded_at), so a single ask could only ever be satisfied by one
document and there was no way to remove a bad upload. This splits those
columns out into their own evidence_files table (one request -> many
files), carries over any file already uploaded under the old model, and
drops the now-redundant columns from evidence_requests. status stays on
evidence_requests, but its meaning is now derived at the application
layer from "does this request have at least one file" rather than
being set once and left alone.

Revision ID: 0067
Revises: 0066
Create Date: 2026-09-17
"""
from alembic import op


revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE evidence_files (
            evidence_file_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            request_id       UUID NOT NULL REFERENCES evidence_requests(request_id) ON DELETE CASCADE,
            file_name        VARCHAR(255) NOT NULL,
            content_type     VARCHAR(100),
            file_data        BYTEA NOT NULL,
            uploaded_by      UUID REFERENCES users(user_id) ON DELETE SET NULL,
            uploaded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_evidence_files_request_id ON evidence_files (request_id)")

    op.execute(
        """
        INSERT INTO evidence_files (request_id, file_name, content_type, file_data, uploaded_by, uploaded_at)
        SELECT request_id, file_name, content_type, file_data, uploaded_by, coalesce(uploaded_at, now())
        FROM evidence_requests
        WHERE file_data IS NOT NULL
        """
    )

    op.execute("ALTER TABLE evidence_requests DROP COLUMN file_name")
    op.execute("ALTER TABLE evidence_requests DROP COLUMN content_type")
    op.execute("ALTER TABLE evidence_requests DROP COLUMN file_data")
    op.execute("ALTER TABLE evidence_requests DROP COLUMN uploaded_by")
    op.execute("ALTER TABLE evidence_requests DROP COLUMN uploaded_at")


def downgrade() -> None:
    op.execute("ALTER TABLE evidence_requests ADD COLUMN file_name VARCHAR(255)")
    op.execute("ALTER TABLE evidence_requests ADD COLUMN content_type VARCHAR(100)")
    op.execute("ALTER TABLE evidence_requests ADD COLUMN file_data BYTEA")
    op.execute("ALTER TABLE evidence_requests ADD COLUMN uploaded_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE evidence_requests ADD COLUMN uploaded_at TIMESTAMPTZ")
    op.execute(
        """
        UPDATE evidence_requests er
        SET file_name = ef.file_name, content_type = ef.content_type, file_data = ef.file_data,
            uploaded_by = ef.uploaded_by, uploaded_at = ef.uploaded_at
        FROM (
            SELECT DISTINCT ON (request_id) request_id, file_name, content_type, file_data, uploaded_by, uploaded_at
            FROM evidence_files
            ORDER BY request_id, uploaded_at DESC
        ) ef
        WHERE er.request_id = ef.request_id
        """
    )
    op.execute("DROP TABLE IF EXISTS evidence_files")
