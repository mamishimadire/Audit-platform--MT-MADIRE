"""Widens test_executions_status_check to allow the new six-state execution
vocabulary — a DB-level CHECK constraint (ANY (ARRAY['running', 'completed',
'failed'])) predating the SQLAlchemy models (never declared there, only
found by querying pg_constraint directly) that has been silently rejecting
every attempt to write 'pass'/'exception'/'mapping_required'/'not_testable'/
'insufficient_data'/'error' since the status-vocabulary code (commit
6a54b1a) deployed.

Consequence while this was live: every execution attempt — both the direct
MongoDB path (app.services.direct_execution_service) and any Gateway-
reported result — failed at db.flush()/commit() with an IntegrityError,
caught by the outer try/except in app.main._direct_execution_loop (or
swallowed by the same pattern in record_execution_report's caller), logged,
and silently retried next cycle forever. No execution has successfully
recorded a NEW-vocabulary result since that deploy; every row visible today
still holds an old completed/failed value (see 0057, which reclassifies
those) until this constraint is fixed and a fresh execution actually runs.

The widened constraint is the UNION of the old and new values, not a swap
— 0057 (the very next migration) reclassifies every existing completed/
failed row, but that update runs as its own migration/transaction, so this
one has to tolerate old values still being present the instant it commits.
'completed'/'failed' staying permanently valid after that is harmless:
nothing written by any code past this point ever produces them again.

Revision ID: 0056
Revises: 0055
Create Date: 2026-09-16
"""
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None

_OLD_VALUES = ("running", "completed", "failed")
_NEW_VALUES = ("pass", "exception", "mapping_required", "not_testable", "insufficient_data", "error")
_ALL_VALUES = _OLD_VALUES + _NEW_VALUES


def upgrade() -> None:
    op.execute("ALTER TABLE test_executions DROP CONSTRAINT test_executions_status_check")
    values = ", ".join(f"'{v}'" for v in _ALL_VALUES)
    op.execute(f"ALTER TABLE test_executions ADD CONSTRAINT test_executions_status_check CHECK (status IN ({values}))")


def downgrade() -> None:
    op.execute("ALTER TABLE test_executions DROP CONSTRAINT test_executions_status_check")
    values = ", ".join(f"'{v}'" for v in _OLD_VALUES)
    op.execute(f"ALTER TABLE test_executions ADD CONSTRAINT test_executions_status_check CHECK (status IN ({values}))")
