"""Immutable versioning for test rules and data mappings, plus execution
traceability back to the exact rule version that ran.

Editing an approved/active rule or mapping now creates a NEW row (new
version, supersedes_*_id pointing at the prior one, prior row's status
flipped to 'superseded') instead of overwriting the live row in place.
History is preserved by construction — nothing is ever deleted, and
"what did this control's test actually check on March 3rd" is answerable
by walking the supersedes chain against timestamps, not by trusting that
nobody edited the row since then.

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-08

"""
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Test rules ---
    op.execute("ALTER TABLE test_rules ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE test_rules ADD COLUMN supersedes_rule_id UUID REFERENCES test_rules(rule_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE test_rules DROP CONSTRAINT test_rules_status_check")
    op.execute(
        "ALTER TABLE test_rules ADD CONSTRAINT test_rules_status_check "
        "CHECK (status IN ('pending_approval', 'active', 'rejected', 'superseded', 'deleted'))"
    )

    # --- Data mappings ---
    op.execute("ALTER TABLE test_data_mappings ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE test_data_mappings ADD COLUMN supersedes_mapping_id UUID REFERENCES test_data_mappings(mapping_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE test_data_mappings ADD COLUMN rejected_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE test_data_mappings ADD COLUMN rejected_at TIMESTAMPTZ")
    op.execute("ALTER TABLE test_data_mappings ADD COLUMN rejected_reason TEXT")
    # A pre-existing inline CHECK (auto-named test_data_mappings_mapping_status_check,
    # predating this migration history) only allowed needs_review/auto/manually_mapped/
    # approved — replace it rather than add a second, differently-named constraint
    # alongside it, which would leave both enforced simultaneously.
    op.execute("ALTER TABLE test_data_mappings DROP CONSTRAINT IF EXISTS test_data_mappings_mapping_status_check")
    op.execute("ALTER TABLE test_data_mappings DROP CONSTRAINT IF EXISTS test_data_mappings_status_check")
    op.execute(
        "ALTER TABLE test_data_mappings ADD CONSTRAINT test_data_mappings_status_check "
        "CHECK (mapping_status IN ('needs_review', 'auto', 'manually_mapped', 'approved', 'rejected', 'superseded'))"
    )

    # --- Execution traceability: which exact rule version produced this run ---
    op.execute("ALTER TABLE test_executions ADD COLUMN rule_id UUID REFERENCES test_rules(rule_id) ON DELETE SET NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE test_executions DROP COLUMN IF EXISTS rule_id")

    op.execute("ALTER TABLE test_data_mappings DROP CONSTRAINT IF EXISTS test_data_mappings_status_check")
    op.execute("ALTER TABLE test_data_mappings DROP COLUMN IF EXISTS rejected_reason")
    op.execute("ALTER TABLE test_data_mappings DROP COLUMN IF EXISTS rejected_at")
    op.execute("ALTER TABLE test_data_mappings DROP COLUMN IF EXISTS rejected_by")
    op.execute("ALTER TABLE test_data_mappings DROP COLUMN IF EXISTS supersedes_mapping_id")
    op.execute("ALTER TABLE test_data_mappings DROP COLUMN IF EXISTS version")

    op.execute("ALTER TABLE test_rules DROP CONSTRAINT test_rules_status_check")
    op.execute(
        "ALTER TABLE test_rules ADD CONSTRAINT test_rules_status_check "
        "CHECK (status IN ('pending_approval', 'active', 'rejected', 'deleted'))"
    )
    op.execute("ALTER TABLE test_rules DROP COLUMN IF EXISTS supersedes_rule_id")
    op.execute("ALTER TABLE test_rules DROP COLUMN IF EXISTS version")
