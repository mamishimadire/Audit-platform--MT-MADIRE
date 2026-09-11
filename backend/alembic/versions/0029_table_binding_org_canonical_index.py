"""Index control_table_bindings by (organization_id, canonical_table_name)

Supports the cross-control "has this canonical table already been bound
somewhere else in this organization?" lookup used to auto-suggest a
binding for a control that shares a table with one already mapped.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-07

"""
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_table_bindings_org_canonical",
        "control_table_bindings",
        ["organization_id", "canonical_table_name"],
    )


def downgrade() -> None:
    op.drop_index("idx_table_bindings_org_canonical", table_name="control_table_bindings")
