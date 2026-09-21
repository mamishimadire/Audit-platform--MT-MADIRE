"""
Read-only guards for the reads this Gateway does on its own initiative (column profiling, relationship
measurement): a ceiling on how long one statement may run and, where the engine has one, a read-only
transaction. Best effort: a guard the server refuses is skipped, never fatal.

Imports nothing from the Gateway (no pandas), so the platform's tests can load this file by path and prove the
platform applies exactly the same guards to a database it reaches itself (data_source_service._apply_read_guards).

SAP HANA has none here: its session statement timeout is not something this code has been able to verify against
a server, and a wrong statement would only be silently skipped, so it is left out rather than guessed.
"""
from sqlalchemy import text

PROFILE_STATEMENT_TIMEOUT_MS = 15_000


def apply_read_guards(conn, dialect_name: str) -> None:
    try:
        if dialect_name == "postgresql":
            conn.execute(text(f"SET LOCAL statement_timeout = {PROFILE_STATEMENT_TIMEOUT_MS}"))
            conn.execute(text("SET TRANSACTION READ ONLY"))
        elif dialect_name == "mysql":
            conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {PROFILE_STATEMENT_TIMEOUT_MS}"))
        elif dialect_name == "oracle":
            # python-oracledb's per-call timeout (milliseconds); SET TRANSACTION must be the transaction's first statement.
            conn.connection.dbapi_connection.call_timeout = PROFILE_STATEMENT_TIMEOUT_MS
            conn.execute(text("SET TRANSACTION READ ONLY"))
        elif dialect_name == "snowflake":
            conn.execute(text(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {PROFILE_STATEMENT_TIMEOUT_MS // 1000}"))
    except Exception:  # noqa: BLE001 — a guard the server refuses must not block profiling or measuring
        conn.rollback()
