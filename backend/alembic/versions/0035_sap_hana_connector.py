"""Add SAP HANA as the fifth direct-connection database engine.

SAP HANA needs one connection-level setting no other engine here has:
whether to require TLS encryption on the wire (hdbcli's `encrypt` /
`sslValidateCertificate` connect args). Every other engine already
connects over TLS-by-default infrastructure (managed Postgres/MySQL/SQL
Server providers, Oracle thin mode) without a user-facing toggle, but
on-premise HANA instances vary, so this defaults to True (encrypted) and
lets the user turn it off only if their instance genuinely doesn't
support it — matching the "encryption should default to enabled" rule.

HANA also doesn't need a per-connection database/tenant name for a normal
tenant-DB connection (see data_source_service._direct_engine_url) — schema
discovery itself needs no schema change, since SQLAlchemy's HANA dialect
(sqlalchemy-hana) implements table/column reflection against
SYS.TABLES/SYS.TABLE_COLUMNS internally — inspect() already works, same as
every other engine.

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-09

"""
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE data_connections ADD COLUMN sap_hana_encrypt BOOLEAN NOT NULL DEFAULT true")


def downgrade() -> None:
    op.execute("ALTER TABLE data_connections DROP COLUMN IF EXISTS sap_hana_encrypt")
