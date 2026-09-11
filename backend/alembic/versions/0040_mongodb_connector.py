"""Add MongoDB as a direct-connection data source.

MongoDB is not relational — there is no fixed table/column shape, so it
deliberately does NOT go through the SQLAlchemy `inspect(engine)` path every
other direct connector shares (see data_source_service.py). It reuses
host/database_name/username/encrypted_password as-is (all already generic
enough — host holds the cluster address, e.g. Atlas's
`mamishi.xxxxx.mongodb.net`) and adds exactly one new column:

  - mongodb_srv (bool, default true) — selects `mongodb+srv://` (a DNS SRV
    lookup resolves the real hosts/ports; `port` is unused — this is what
    every MongoDB Atlas cluster uses) vs a plain `mongodb://host:port/`
    connection string for a self-hosted/replica-set deployment where the
    caller supplies an explicit port.

Discovery is handled by app/services/mongo_connector.py: it lists
collections, samples up to 100 documents per collection (via $sample),
flattens nested objects into dotted paths (e.g. `employee.department.name`),
and infers a field's type/optionality from what was actually observed across
the sample — never presented as a guaranteed schema, only an inferred one.

Revision ID: 0040
Revises: 0039
Create Date: 2026-09-11

"""
from alembic import op


revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE data_connections ADD COLUMN mongodb_srv BOOLEAN NOT NULL DEFAULT true")


def downgrade() -> None:
    op.execute("ALTER TABLE data_connections DROP COLUMN IF EXISTS mongodb_srv")
