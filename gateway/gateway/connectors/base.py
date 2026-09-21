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

from gateway import relationships
from gateway.connectors import guards
from gateway.schema_metadata import derive_column_constraints, normalize_foreign_keys


def _batched(inspector, method_name: str, schema: str | None):
    """{table name: result} from one of the inspector's get_multi_* calls, or None when the dialect or the
    call can't do it (the caller then reads table by table)."""
    try:
        return {table: result for (_schema, table), result in getattr(inspector, method_name)(schema=schema).items()}
    except Exception:  # noqa: BLE001 — unsupported, or a permission gap on one catalogue view
        return None


def _safe(call, table_name: str, **kwargs):
    try:
        return call(table_name, **kwargs)
    except Exception:  # noqa: BLE001 — unsupported by this database or not permitted; discovery carries on without it
        return None


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
    # Oracle only: is `database` a Service Name (default, the modern form) or a SID?
    oracle_connection_type: str = "service_name"
    # SAP HANA only: require TLS and validate the server certificate.
    sap_hana_encrypt: bool = True
    # Snowflake only. `host` is the account identifier, `database` the database and `schema` the schema.
    # With snowflake_auth_method "key_pair", the PEM private key (and its passphrase, if it has one) replace the password.
    snowflake_warehouse: str | None = None
    snowflake_role: str | None = None
    snowflake_auth_method: str = "password"
    snowflake_private_key: str | None = None
    snowflake_key_passphrase: str | None = None


class SqlAlchemyConnector:
    # What a connection test runs. Oracle and HANA have no SELECT without a FROM.
    ping_sql = "SELECT 1"

    def __init__(self, config: ConnectorConfig):
        self.config = config
        self._engine: Engine = create_engine(self.build_url(), connect_args=self.connect_args(), pool_pre_ping=True)

    def build_url(self) -> str:
        raise NotImplementedError

    def connect_args(self) -> dict:
        """Driver arguments that cannot go in the URL (timeouts, TLS, key material). None by default."""
        return {}

    def test_connection(self) -> tuple[bool, str | None]:
        try:
            with self._engine.connect() as conn:
                conn.execute(text(self.ping_sql))
            return True, None
        except Exception as exc:  # noqa: BLE001 — a failed test must never look like a crash
            return False, str(exc)

    def discover(self) -> list[dict]:
        """Returns entities in the exact shape the platform's DiscoveryPayload schema expects."""
        inspector = inspect(self._engine)
        schema = self.config.schema
        entities: list[dict] = []
        # One catalogue query per FACT for the whole schema (SQLAlchemy 2.0's get_multi_*) instead of one per
        # fact PER TABLE: every one is a network round trip, which is what makes discovery of a big schema slow
        # (Snowflake and Oracle most of all). A dialect or call that can't batch falls back to the per-table read
        # of that fact alone, so what is discovered is identical either way.
        columns_by = _batched(inspector, "get_multi_columns", schema)
        pks_by = _batched(inspector, "get_multi_pk_constraint", schema)
        uniques_by = _batched(inspector, "get_multi_unique_constraints", schema)
        indexes_by = _batched(inspector, "get_multi_indexes", schema)
        fks_by = _batched(inspector, "get_multi_foreign_keys", schema)
        for table_name in inspector.get_table_names(schema=schema):
            columns = columns_by[table_name] if columns_by is not None and table_name in columns_by else inspector.get_columns(table_name, schema=schema)
            pk_constraint = (pks_by[table_name] if pks_by is not None and table_name in pks_by else inspector.get_pk_constraint(table_name, schema=schema)) or {}
            pk_columns = set(pk_constraint.get("constrained_columns") or [])
            # Catalog facts beyond names/types. Each read is best-effort: a
            # database that can't report one (or a permission gap) must never
            # fail discovery; the fact is simply not reported.
            unique_constraints = uniques_by[table_name] if uniques_by is not None and table_name in uniques_by else _safe(inspector.get_unique_constraints, table_name, schema=schema)
            indexes = indexes_by[table_name] if indexes_by is not None and table_name in indexes_by else _safe(inspector.get_indexes, table_name, schema=schema)
            raw_fks = fks_by[table_name] if fks_by is not None and table_name in fks_by else _safe(inspector.get_foreign_keys, table_name, schema=schema)
            foreign_keys = normalize_foreign_keys(raw_fks)
            facts = derive_column_constraints(
                [col["name"] for col in columns],
                nullable_by_column={col["name"]: col.get("nullable") for col in columns},
                pk_columns=pk_columns,
                unique_constraints=unique_constraints,
                indexes=indexes,
            )
            fields = [
                {
                    "field_name": col["name"],
                    "data_type": str(col["type"]),
                    "is_primary_key": col["name"] in pk_columns,
                    "is_sensitive": False,  # sensitivity is an auditor judgement call, not auto-detected
                    "is_nullable": facts[col["name"]]["is_nullable"],
                    "is_unique": facts[col["name"]]["is_unique"] if (unique_constraints is not None or pk_columns) else None,
                    "is_indexed": facts[col["name"]]["is_indexed"] if indexes is not None else None,
                }
                for col in columns
            ]
            entity = {"entity_name": table_name, "entity_type": "table", "fields": fields}
            if foreign_keys is not None:
                entity["foreign_keys"] = foreign_keys
            entities.append(entity)
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
            self._apply_read_guards(conn)
            return [dict(row._mapping) for row in conn.execute(statement)]

    def _apply_read_guards(self, conn) -> None:
        guards.apply_read_guards(conn, self._engine.dialect.name)

    def measure_relationships(self, pairs: list[dict]) -> list[dict]:
        """Counts (never values) for each requested column pair, on this database: how many
        distinct child values there are and how many of them exist on the parent side (see
        gateway/relationships.py). Same read-only guards as sample_rows."""

        def one(pair: dict) -> dict | None:
            with self._engine.connect() as conn:
                self._apply_read_guards(conn)
                return relationships.measure_sql(conn, pair, self.config.schema)

        return relationships.measure_all(one, pairs)

    def close(self) -> None:
        self._engine.dispose()
