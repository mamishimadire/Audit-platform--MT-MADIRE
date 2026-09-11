"""Widen data_entities.entity_type to allow 'collection' (MongoDB).

The original CHECK constraint (database/schema.sql) allows
'table'|'view'|'api'|'file' — it anticipated HubSpot's 'api' shape but not
MongoDB's, discovered when live-testing migration 0040's connector: every
insert from mongo_connector.py's discovery (entity_type='collection') was
rejected outright by this constraint. This is a narrow, additive widening —
existing rows/values are untouched, only one new legal value is added.

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-11

"""
from alembic import op


revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE data_entities DROP CONSTRAINT IF EXISTS data_entities_entity_type_check")
    op.execute(
        "ALTER TABLE data_entities ADD CONSTRAINT data_entities_entity_type_check "
        "CHECK (entity_type IN ('table', 'view', 'api', 'file', 'collection'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE data_entities DROP CONSTRAINT IF EXISTS data_entities_entity_type_check")
    op.execute(
        "ALTER TABLE data_entities ADD CONSTRAINT data_entities_entity_type_check "
        "CHECK (entity_type IN ('table', 'view', 'api', 'file'))"
    )
