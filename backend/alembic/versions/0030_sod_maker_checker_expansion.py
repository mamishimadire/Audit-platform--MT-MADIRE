"""Real maker-checker for test rules and control activation/deactivation,
plus tracking who completed a remediation action (needed to enforce
performer != verifier at retest time).

This mirrors the exact pattern already proven for test_data_mappings
(migration 0025 + mapping_service.approve_mapping): a status column gets a
'pending_approval' state, an approved_by/approved_at pair records the
checker, and the service layer rejects approver == creator by identity,
not by role.

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-08

"""
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Test rules: create -> pending_approval -> active/rejected ---
    op.execute("ALTER TABLE test_rules ADD COLUMN approved_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE test_rules ADD COLUMN approved_at TIMESTAMPTZ")
    op.execute("ALTER TABLE test_rules ADD COLUMN rejected_reason TEXT")
    op.execute("ALTER TABLE test_rules DROP CONSTRAINT test_rules_status_check")
    op.execute(
        "ALTER TABLE test_rules ADD CONSTRAINT test_rules_status_check "
        "CHECK (status IN ('pending_approval', 'active', 'rejected', 'deleted'))"
    )
    # Rules created before this migration were never submitted for approval
    # under the old model — grandfather them in as 'active' rather than
    # silently freezing every existing control's test overnight.
    op.execute("UPDATE test_rules SET status = 'active' WHERE status NOT IN ('pending_approval', 'active', 'rejected', 'deleted')")

    # --- Controls: activation and deactivation both go through a request/approve pair ---
    op.execute("ALTER TABLE controls ADD COLUMN activation_requested_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE controls ADD COLUMN activation_approved_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE controls ADD COLUMN deactivation_requested_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE controls ADD COLUMN deactivation_requested_reason TEXT")
    op.execute("ALTER TABLE controls ADD COLUMN deactivation_approved_by UUID REFERENCES users(user_id) ON DELETE SET NULL")
    op.execute("ALTER TABLE controls DROP CONSTRAINT controls_status_check")
    op.execute(
        "ALTER TABLE controls ADD CONSTRAINT controls_status_check "
        "CHECK (status IN ('pending_mapping', 'pending_activation', 'active', 'pending_deactivation', 'inactive', 'retired'))"
    )

    # --- Remediation: record who actually completed it, distinct from who was assigned ---
    op.execute("ALTER TABLE remediation_actions ADD COLUMN completed_by UUID REFERENCES users(user_id) ON DELETE SET NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE remediation_actions DROP COLUMN IF EXISTS completed_by")
    op.execute("ALTER TABLE controls DROP CONSTRAINT IF EXISTS controls_status_check")
    op.execute(
        "ALTER TABLE controls ADD CONSTRAINT controls_status_check "
        "CHECK (status IN ('pending_mapping','active','inactive','retired'))"
    )
    op.execute("ALTER TABLE controls DROP COLUMN IF EXISTS deactivation_approved_by")
    op.execute("ALTER TABLE controls DROP COLUMN IF EXISTS deactivation_requested_reason")
    op.execute("ALTER TABLE controls DROP COLUMN IF EXISTS deactivation_requested_by")
    op.execute("ALTER TABLE controls DROP COLUMN IF EXISTS activation_approved_by")
    op.execute("ALTER TABLE controls DROP COLUMN IF EXISTS activation_requested_by")
    op.execute("ALTER TABLE test_rules DROP CONSTRAINT test_rules_status_check")
    op.execute(
        "ALTER TABLE test_rules ADD CONSTRAINT test_rules_status_check "
        "CHECK (status IN ('active', 'deleted'))"
    )
    op.execute("ALTER TABLE test_rules DROP COLUMN IF EXISTS rejected_reason")
    op.execute("ALTER TABLE test_rules DROP COLUMN IF EXISTS approved_at")
    op.execute("ALTER TABLE test_rules DROP COLUMN IF EXISTS approved_by")
