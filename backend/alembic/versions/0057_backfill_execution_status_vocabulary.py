"""Reclassifies every existing test_executions row still holding the old
binary completed/failed status into the new six-state vocabulary (see
app/core/execution_status.py), using the exact same rule the new code uses
going forward:

  status='failed'                                  -> 'error'
  status='completed', records_analyzed in (NULL,0)  -> 'insufficient_data'
  status='completed', exceptions_found > 0          -> 'exception'
  status='completed', exceptions_found in (NULL,0)   -> 'pass'

Without this, a row written before the status-vocabulary code deployed
stays permanently invisible to every new-vocabulary filter, count, and
per-row explanation on the Executions page — not because anything is
broken today, but because the column just holds whatever string was
written at the time, and old rows don't get new runs until their
schedule's next_run naturally comes due (hours or days away for a daily
schedule). This makes the fix immediate instead of "wait and see."

Checked against the live data before writing this migration: every
'failed' row's execution_log (267 TLS-handshake, 2 misconfigured, 2
timeout, 11 blank) was a genuine connectivity/infrastructure problem, not
a single one referencing a missing field mapping — so 'error' is the
correct reclassification for all 282, with no mapping_required cases to
carve out. Of 106 'completed' rows, 86 had exceptions_found > 0 (were
actually 'exception' all along, just labeled 'completed' by the old
binary vocabulary) and 21 were genuinely clean ('pass'); none had zero
records_analyzed.

Only rows still holding the literal old strings are touched — an
execution already written in the new vocabulary (by code that had
already deployed) is left alone, and running this twice is a no-op the
second time.

Revision ID: 0057
Revises: 0056
Create Date: 2026-09-16
"""
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE test_executions SET status = 'error' WHERE status = 'failed'")
    op.execute(
        "UPDATE test_executions SET status = 'insufficient_data' "
        "WHERE status = 'completed' AND (records_analyzed IS NULL OR records_analyzed = 0)"
    )
    op.execute(
        "UPDATE test_executions SET status = 'exception' "
        "WHERE status = 'completed' AND records_analyzed > 0 AND COALESCE(exceptions_found, 0) > 0"
    )
    op.execute(
        "UPDATE test_executions SET status = 'pass' "
        "WHERE status = 'completed' AND records_analyzed > 0 AND COALESCE(exceptions_found, 0) = 0"
    )


def downgrade() -> None:
    # Necessarily lossy — a genuinely new 'error'/'pass'/'exception'/
    # 'insufficient_data' row written by post-deploy code is indistinguishable
    # from one this migration reclassified, so downgrading collapses both
    # back to the old binary vocabulary rather than restoring exactly what
    # was there before upgrade().
    op.execute("UPDATE test_executions SET status = 'failed' WHERE status = 'error'")
    op.execute(
        "UPDATE test_executions SET status = 'completed' "
        "WHERE status IN ('pass', 'exception', 'insufficient_data', 'mapping_required', 'not_testable')"
    )
