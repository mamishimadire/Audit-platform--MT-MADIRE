"""Control-to-table binding, rule templates, and rule provenance

Enhancement 1: per-organization binding of a control's required canonical
tables to an actual discovered table (or an explicit "not applicable" with
a logged reason) — gates control activation.

Enhancement 3 infrastructure: a control_library entry can carry a rule
template (the exact same rule_definition shape test_rules already uses);
generating a test_rule from one is an explicit auditor action, tagged with
its origin, never applied silently. Rules are soft-deleted (status
'deleted' + a mandatory reason) — never hard-deleted, consistent with this
platform's evidence-lineage principle.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-07

"""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE control_table_bindings (
            binding_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
            control_id UUID NOT NULL REFERENCES controls(control_id) ON DELETE CASCADE,
            canonical_table_name VARCHAR(100) NOT NULL,
            data_source_id UUID REFERENCES data_sources(data_source_id) ON DELETE SET NULL,
            entity_id UUID REFERENCES data_entities(entity_id) ON DELETE SET NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'bound',
            not_applicable_reason TEXT,
            bound_by UUID REFERENCES users(user_id) ON DELETE SET NULL,
            bound_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (control_id, canonical_table_name)
        )
        """
    )
    op.execute("CREATE INDEX ix_control_table_bindings_control ON control_table_bindings (control_id)")

    op.execute(
        """
        CREATE TABLE control_rule_templates (
            template_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            control_library_id UUID NOT NULL UNIQUE REFERENCES control_library(control_library_id) ON DELETE CASCADE,
            rule_name VARCHAR(255) NOT NULL,
            rule_definition TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute("ALTER TABLE test_rules ADD COLUMN origin VARCHAR(30) NOT NULL DEFAULT 'manual'")
    op.execute("ALTER TABLE test_rules ADD COLUMN template_id UUID REFERENCES control_rule_templates(template_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE test_rules ADD COLUMN edited_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE test_rules ADD COLUMN edited_at TIMESTAMPTZ")
    op.execute("ALTER TABLE test_rules ADD COLUMN needs_review BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE test_rules ADD COLUMN deleted_reason TEXT")
    op.execute("ALTER TABLE test_rules ADD COLUMN deleted_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE test_rules ADD COLUMN deleted_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE test_rules DROP COLUMN IF EXISTS deleted_at")
    op.execute("ALTER TABLE test_rules DROP COLUMN IF EXISTS deleted_by")
    op.execute("ALTER TABLE test_rules DROP COLUMN IF EXISTS deleted_reason")
    op.execute("ALTER TABLE test_rules DROP COLUMN IF EXISTS needs_review")
    op.execute("ALTER TABLE test_rules DROP COLUMN IF EXISTS edited_at")
    op.execute("ALTER TABLE test_rules DROP COLUMN IF EXISTS edited_by")
    op.execute("ALTER TABLE test_rules DROP COLUMN IF EXISTS template_id")
    op.execute("ALTER TABLE test_rules DROP COLUMN IF EXISTS origin")
    op.execute("DROP TABLE IF EXISTS control_rule_templates")
    op.execute("DROP TABLE IF EXISTS control_table_bindings")
