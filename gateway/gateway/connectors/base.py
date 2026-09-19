"""
Layer 2 — Database Adapter (product spec Section 17). Discovery is
implemented once here, generically, via SQLAlchemy's reflection API —
every engine-specific subclass only has to know how to build a connection
URL. This is what lets a brand-new database engine be added as "one new
connector against the shared interface, not rebuilding the platform."
"""
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy import column as sql_column, table as sql_table
from sqlalchemy.engine import Engine

_PROFILE_STATEMENT_TIMEOUT_MS = 15_000


@dataclass
class ConnectorConfig:
    host: str
    port: int
    database: str
    username: str
    password: str
    schema: str | None = None
    # MongoDB only (see connectors/mongodb.py) — Atlas and most managed
    # MongoDB hosts use a mongodb+srv:// URI (DNS-based, no explicit port);
    # a self-hosted MongoDB typically uses a plain mongodb:// URI with one.
    # Ignored entirely by every SQL connector.
    mongodb_srv: bool = False


class SqlAlchemyConnector:
    def __init__(self, config: ConnectorConfig):
        self.config = config
        self._engine: Engine = create_engine(self.build_url(), pool_pre_ping=True)

    def build_url(self) -> str:
        raise NotImplementedError

    def test_connection(self) -> tuple[bool, str | None]:
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True, None
        except Exception as exc:  # noqa: BLE001 — a failed test must never look like a crash
            return False, str(exc)

    def discover(self) -> list[dict]:
        """Returns entities in the exact shape the platform's DiscoveryPayload schema expects."""
        inspector = inspect(self._engine)
        schema = self.config.schema
        entities: list[dict] = []
        for table_name in inspector.get_table_names(schema=schema):
            columns = inspector.get_columns(table_name, schema=schema)
            pk_constraint = inspector.get_pk_constraint(table_name, schema=schema) or {}
            pk_columns = set(pk_constraint.get("constrained_columns") or [])
            fields = [
                {
                    "field_name": col["name"],
                    "data_type": str(col["type"]),
                    "is_primary_key": col["name"] in pk_columns,
                    "is_sensitive": False,  # sensitivity is an auditor judgement call, not auto-detected
                }
                for col in columns
            ]
            entities.append({"entity_name": table_name, "entity_type": "table", "fields": fields})
        return entities

    def fetch_dataframe(self, entity_name: str, columns: list[str]) -> pd.DataFrame:
        """
        Reads only the mapped columns the audit test actually needs — never
        a full unscoped table dump (product spec's data-minimization
        principle, Section 24/25). Built via reflected Table + select() so
        identifier quoting is correct per-dialect (Postgres/MySQL/SQL
        Server each quote differently) instead of hand-rolled SQL strings.
        """
        metadata = MetaData()
        table = Table(entity_name, metadata, autoload_with=self._engine, schema=self.config.schema)
        stmt = select(*(table.c[name] for name in columns))
        return pd.read_sql(stmt, self._engine)

    def sample_rows(self, entity_name: str, columns: list[tuple[str, str | None]], limit: int) -> list[dict]:
        """The first `limit` rows of the named (column, declared type) pairs,
        for column profiling (see gateway/profiling.py). One query, built
        from table()/column() constructs so identifiers are still quoted
        per-dialect but no reflection round trips are spent on it. Read-only
        by construction (a single SELECT), plus a statement timeout and a
        read-only transaction where the engine supports them; a guard the
        server rejects is skipped rather than fatal."""
        names = [name for name, _ in columns]
        if not names:
            return []
        statement = (
            select(*(sql_column(name) for name in names))
            .select_from(sql_table(entity_name, schema=self.config.schema))
            .limit(limit)
        )
        with self._engine.connect() as conn:
            try:
                dialect = self._engine.dialect.name
                if dialect == "postgresql":
                    conn.execute(text(f"SET LOCAL statement_timeout = {_PROFILE_STATEMENT_TIMEOUT_MS}"))
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                elif dialect == "mysql":
                    conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {_PROFILE_STATEMENT_TIMEOUT_MS}"))
            except Exception:  # noqa: BLE001 — a guard the server refuses must not block profiling
                conn.rollback()
            return [dict(row._mapping) for row in conn.execute(statement)]

    def close(self) -> None:
        self._engine.dispose()
