"""Corrects 11 test_executions rows that 0057's backfill wrongly classified
as 'error'.

0057 assumed every historical 'failed' row meant the same thing: a
connectivity/infrastructure problem, based on checking direct_execution_
service's execution_log patterns (267 TLS-handshake, 2 misconfigured, 2
timeout, 11 blank). What that check missed: device_compliance_service.py
and software_compliance_service.py — a completely separate execution path
— used the SAME literal string 'failed' to mean something else entirely:
"this device/software check found real problems" (today's 'exception'),
never setting execution_log at all. Those 11 rows have records_analyzed > 0
and exceptions_found > 0 with execution_log IS NULL — a genuine technical
error from the MongoDB/direct path never gets that far (records_analyzed
is always NULL when the connection itself fails), so this combination
uniquely and safely identifies the misclassified compliance rows.

Revision ID: 0058
Revises: 0057
Create Date: 2026-09-16
"""
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE test_executions
        SET status = 'exception'
        WHERE status = 'error'
          AND execution_log IS NULL
          AND records_analyzed > 0
          AND COALESCE(exceptions_found, 0) > 0
        """
    )
    op.execute(
        """
        UPDATE test_executions
        SET status = 'pass'
        WHERE status = 'error'
          AND execution_log IS NULL
          AND records_analyzed > 0
          AND COALESCE(exceptions_found, 0) = 0
        """
    )


def downgrade() -> None:
    # Lossy in the same way 0057's downgrade is — cannot distinguish these
    # rows from a genuinely new exception/pass written after this point.
    op.execute(
        """
        UPDATE test_executions
        SET status = 'error'
        WHERE status IN ('exception', 'pass')
          AND execution_log IS NULL
          AND records_analyzed > 0
        """
    )
