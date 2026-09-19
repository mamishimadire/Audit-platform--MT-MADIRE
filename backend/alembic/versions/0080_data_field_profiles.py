"""Column value profiles — what a discovered column actually holds.

Name similarity can say a column looks like `user.status`; it cannot say it
is full of movie genres, or that a `last_login` is free text. A profile is a
small summary of a sample of the column's values (see
app/core/value_profile.py), stored beside the discovered column so mapping
and table binding can check the KIND of data, not just the name.

Kept in its own table, one row per field, rather than as columns on
data_fields: re-discovery reconciles data_fields by name and must never wipe
a profile, and data_fields has no updated_at to say how fresh one is.

Privacy (enforced in build_column_profile before anything reaches this
table): real values are stored in `top_values` only for small, non-sensitive,
enum-like columns (<= 20 distinct values, <= 40 characters each). Sensitive
and PII-named columns, identifiers, timestamps and anything high-cardinality
keep statistics only, with `top_values` NULL.

Additive: a new table only, nothing existing changes, so production is safe
during the deploy window.

Revision ID: 0080
Revises: 0079
Create Date: 2026-09-19
"""
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data_field_profiles (
            field_id        UUID PRIMARY KEY REFERENCES data_fields(field_id) ON DELETE CASCADE,
            sample_size     INTEGER NOT NULL,
            null_ratio      DOUBLE PRECISION NOT NULL,
            distinct_count  INTEGER NOT NULL,
            distinct_ratio  DOUBLE PRECISION NOT NULL,
            value_kind      VARCHAR(20),
            top_values      JSONB,
            max_length      INTEGER,
            profiled_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data_field_profiles")
