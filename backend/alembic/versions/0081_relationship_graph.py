"""Relationship graph — how a client's tables relate, not just what they hold.

Column names, types and value profiles (0080) say what a column IS. To prove a
control the platform also needs to know how tables JOIN: which column of
api_access references which column of users, whether that relationship is
declared in the schema or only inferred from the data, and how much evidence
backs it.

Two additive tables, both keyed to data_fields so re-discovery (which
reconciles fields by name and keeps their ids) never orphans them:

  data_field_constraints   one row per column: nullable / unique / indexed.
  data_relationships       one row per column pair: child -> parent, either
                           `declared_fk` (read from the client's schema) or
                           `inferred` (value containment measured on the
                           client's data). Evidence (containment %, distinct
                           counts, whether the parent side is unique) is kept
                           with the row. `status` lets an auditor confirm or
                           reject an edge, and neither re-discovery nor
                           re-inference ever overwrites a confirmed/rejected one.

Only aggregate numbers are stored for inferred edges — never the values.

Additive: new tables only, nothing existing changes, so production is safe
during the deploy window.

Revision ID: 0081
Revises: 0080
Create Date: 2026-09-19
"""
from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data_field_constraints (
            field_id     UUID PRIMARY KEY REFERENCES data_fields(field_id) ON DELETE CASCADE,
            is_nullable  BOOLEAN,
            is_unique    BOOLEAN NOT NULL DEFAULT false,
            is_indexed   BOOLEAN NOT NULL DEFAULT false
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data_relationships (
            relationship_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            data_source_id   UUID NOT NULL REFERENCES data_sources(data_source_id) ON DELETE CASCADE,
            child_field_id   UUID NOT NULL REFERENCES data_fields(field_id) ON DELETE CASCADE,
            parent_field_id  UUID NOT NULL REFERENCES data_fields(field_id) ON DELETE CASCADE,
            kind             VARCHAR(20) NOT NULL CHECK (kind IN ('declared_fk', 'inferred')),
            constraint_name  VARCHAR(200),
            containment      DOUBLE PRECISION,
            child_distinct   INTEGER,
            parent_distinct  INTEGER,
            parent_unique    BOOLEAN,
            cardinality      VARCHAR(20),
            confidence       DOUBLE PRECISION,
            evidence         JSONB,
            status           VARCHAR(20) NOT NULL DEFAULT 'detected' CHECK (status IN ('detected', 'confirmed', 'rejected')),
            detected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (child_field_id, parent_field_id, kind)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_data_relationships_source ON data_relationships(data_source_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_data_relationships_child ON data_relationships(child_field_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_data_relationships_parent ON data_relationships(parent_field_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data_relationships")
    op.execute("DROP TABLE IF EXISTS data_field_constraints")
