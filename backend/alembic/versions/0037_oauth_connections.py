"""OAuth-based API connectors (HubSpot first) — a genuinely separate
connection family from the SQL direct/gateway connectors, not forced
through data_connections' DB-specific columns.

Why a new table instead of more columns on data_connections: every DB
engine so far (Oracle, SAP HANA) added one or two quirky columns to that
table because they're still fundamentally "host/port/credential" shaped.
An OAuth connection is a different lifecycle entirely — it has its own
token pair, its own expiry/refresh cycle, and its own revocation model
(a client can revoke access from the vendor's side at any time, outside
this platform). Cramming that onto data_connections would mean every
non-OAuth row carries a dozen always-NULL OAuth columns, and every OAuth
row carries a dozen always-NULL host/port/password columns.

data_connections.connection_mode gains a third value, 'oauth' (no CHECK
constraint exists on that column today, so no migration needed there) —
the row still represents "this data source's one active connection,"
just backed by an oauth_connections row instead of inline credentials.

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-09

"""
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE oauth_connections (
            oauth_connection_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            connection_id          UUID NOT NULL UNIQUE REFERENCES data_connections(connection_id) ON DELETE CASCADE,
            organization_id        UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
            connector_type         VARCHAR(50) NOT NULL,
            encrypted_access_token TEXT,
            encrypted_refresh_token TEXT,
            expires_at             TIMESTAMPTZ,
            scope                  TEXT,
            authorization_status   VARCHAR(20) NOT NULL DEFAULT 'pending'
                                    CHECK (authorization_status IN ('pending', 'authorized', 'expired', 'revoked')),
            -- CSRF token for the authorize->callback round trip; cleared once
            -- the callback consumes it, so it can never be replayed.
            oauth_state            VARCHAR(255),
            created_by             UUID REFERENCES users(user_id) ON DELETE SET NULL,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_oauth_connections_org ON oauth_connections(organization_id)")
    op.execute("CREATE INDEX idx_oauth_connections_state ON oauth_connections(oauth_state)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS oauth_connections")
