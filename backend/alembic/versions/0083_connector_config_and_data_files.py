"""Generic connector settings and uploaded data files.

Database connections keep their own columns (host, port, oracle_*, snowflake_*, mongodb_srv...). The
new connection families are not databases: a CSV/Excel file source, an SFTP drop, a REST or SOAP API.
They need a place for their own settings without a column per family:

  data_connections.connector_config   JSONB  non-secret, family-specific settings (base URL, endpoint
                                             definitions, SFTP host/path/pattern, pinned host-key
                                             fingerprint...). Secrets (API keys, tokens, passwords,
                                             private keys) are NEVER stored here: they go, encrypted as
                                             one blob, in the existing encrypted_password column.

  data_files                          the bytes of every file uploaded to a file connection, inline in
                                      Postgres like evidence files (Render's disk is ephemeral, and
                                      these are individual documents). A new upload of the same file
                                      name is a new VERSION: the previous one is kept, `is_current`
                                      flips, so mappings (keyed by the file's name) survive re-uploads
                                      and every version keeps its sha256 for the audit trail.

Additive: one nullable column and one new table.

Revision ID: 0083
Revises: 0082
Create Date: 2026-09-20
"""
from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE data_connections ADD COLUMN IF NOT EXISTS connector_config JSONB")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data_files (
            file_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            connection_id  UUID NOT NULL REFERENCES data_connections(connection_id) ON DELETE CASCADE,
            file_name      VARCHAR(255) NOT NULL,
            content_type   VARCHAR(100),
            sha256         VARCHAR(64) NOT NULL,
            size_bytes     INTEGER NOT NULL,
            file_data      BYTEA NOT NULL,
            is_current     BOOLEAN NOT NULL DEFAULT true,
            uploaded_by    UUID REFERENCES users(user_id) ON DELETE SET NULL,
            uploaded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_data_files_connection ON data_files(connection_id, file_name, is_current)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data_files")
    op.execute("ALTER TABLE data_connections DROP COLUMN IF EXISTS connector_config")
