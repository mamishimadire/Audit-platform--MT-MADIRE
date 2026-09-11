"""Control library: global pre-built control catalogue + activate-from-library

The client asked that controls "must be automatically created populated and
in their own categories, no need to create control manually — we will select
the control to be run/tested and confirm tables and mappings." This migration
adds the reference table that makes that possible:

  control_library  — ~20 audit domains, ~157 pre-built controls, each with
                      its audit procedure and the source tables it needs.
                      Global (not organization-scoped) — every client browses
                      the same list.

`controls` gains a nullable `control_library_id` FK back to it, and a new
`pending_mapping` status — an organization "activates" a library control
(creating a `controls` row referencing it) before confirming its data source
tables/field mappings, at which point it can move to `active`.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-05

"""
import json

from alembic import op

from app.core.control_library_data import (
    CONTROL_LIBRARY,
    DEFAULT_CONTROL_FREQUENCY,
    DEFAULT_CONTROL_NATURE,
    DEFAULT_CONTROL_TYPE,
)

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _escape(value: str) -> str:
    return value.replace("'", "''")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE control_library (
            control_library_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            domain                  VARCHAR(100) NOT NULL,
            control_code            VARCHAR(20) NOT NULL UNIQUE,
            control_name            VARCHAR(255) NOT NULL,
            audit_procedure         TEXT NOT NULL,
            required_tables         JSONB NOT NULL DEFAULT '[]',
            default_control_type    VARCHAR(20),
            default_control_nature  VARCHAR(20),
            default_control_frequency VARCHAR(30)
        )
        """
    )
    op.execute("CREATE INDEX idx_control_library_domain ON control_library(domain)")

    rows = []
    for domain, code, name, procedure, tables in CONTROL_LIBRARY:
        rows.append(
            "('{domain}', '{code}', '{name}', '{procedure}', '{tables}'::jsonb, "
            "'{ctype}', '{nature}', '{frequency}')".format(
                domain=_escape(domain),
                code=_escape(code),
                name=_escape(name),
                procedure=_escape(procedure),
                tables=_escape(json.dumps(tables)),
                ctype=DEFAULT_CONTROL_TYPE,
                nature=DEFAULT_CONTROL_NATURE,
                frequency=DEFAULT_CONTROL_FREQUENCY,
            )
        )
    values_sql = ",\n".join(rows)
    op.execute(
        "INSERT INTO control_library "
        "(domain, control_code, control_name, audit_procedure, required_tables, "
        "default_control_type, default_control_nature, default_control_frequency) "
        f"VALUES {values_sql}"
    )

    op.execute(
        """
        ALTER TABLE controls
            ADD COLUMN control_library_id UUID REFERENCES control_library(control_library_id) ON DELETE SET NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX idx_controls_org_library_unique
            ON controls(organization_id, control_library_id)
            WHERE control_library_id IS NOT NULL
        """
    )
    op.execute("ALTER TABLE controls DROP CONSTRAINT controls_status_check")
    op.execute(
        """
        ALTER TABLE controls
            ADD CONSTRAINT controls_status_check
            CHECK (status IN ('pending_mapping','active','inactive','retired'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE controls DROP CONSTRAINT controls_status_check")
    op.execute(
        """
        ALTER TABLE controls
            ADD CONSTRAINT controls_status_check
            CHECK (status IN ('active','inactive','retired'))
        """
    )
    op.execute("DROP INDEX IF EXISTS idx_controls_org_library_unique")
    op.execute("ALTER TABLE controls DROP COLUMN IF EXISTS control_library_id")
    op.execute("DROP TABLE IF EXISTS control_library")
