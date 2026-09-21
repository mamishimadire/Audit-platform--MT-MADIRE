"""Oracle, SAP HANA and Snowflake in the Gateway. Run from gateway/:  .venv\\Scripts\\python.exe -m unittest discover -s tests -v

WHAT THIS PROVES, AND WHAT IT CANNOT: everything here runs without a database server. It proves the connection
URLs and driver arguments are exactly what each driver documents, that the SQL the Gateway sends compiles for each
engine's own SQLAlchemy dialect into what that engine accepts, that the guards, the configuration loader and the
discovery logic behave, and that the platform builds the same connection from the same settings (see the
backend's test_gateway_engine_parity.py). It does NOT prove a connection to a real Oracle, HANA or Snowflake
server: none was available. The first run against a real one is the remaining test.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import column as sql_column, create_engine, select, table as sql_table, text
from sqlalchemy.dialects import oracle
from sqlalchemy.engine import make_url

from gateway import relationships
from gateway.config import load_settings
from gateway.connectors import _REGISTRY, build_connector, urls
from gateway.connectors import base as base_module
from gateway.connectors.base import ConnectorConfig, SqlAlchemyConnector


def config(**overrides) -> ConnectorConfig:
    defaults = dict(host="db.example.com", port=1521, database="ORCLPDB1", username="auditor", password="p@ss:w/ord%25")
    defaults.update(overrides)
    return ConnectorConfig(**defaults)


def pem(passphrase: bytes | None = None) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    protection = serialization.BestAvailableEncryption(passphrase) if passphrase else serialization.NoEncryption()
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, protection).decode()


class UrlTests(unittest.TestCase):
    def test_oracle_service_name_goes_in_the_query_and_a_sid_in_the_path(self):
        service = urls.oracle_url(config())
        self.assertEqual(str(service), "oracle+oracledb://auditor:***@db.example.com:1521?service_name=ORCLPDB1")
        sid = urls.oracle_url(config(oracle_connection_type="sid", database="ORCL"))
        self.assertEqual(str(sid), "oracle+oracledb://auditor:***@db.example.com:1521/ORCL")
        self.assertEqual(urls.oracle_url(config(oracle_connection_type=None)).query, service.query)  # unset means the modern default

    def test_a_password_with_special_characters_survives_every_url(self):
        for url in (
            urls.oracle_url(config()), urls.hana_url(config(port=39013)),
            urls.snowflake_url(config(host="xy12345", schema="PUBLIC", snowflake_warehouse="WH")),
        ):
            self.assertEqual(url.password, "p@ss:w/ord%25")
            rendered = make_url(url.render_as_string(hide_password=False))  # and it round-trips through text
            self.assertEqual(rendered.password, "p@ss:w/ord%25")

    def test_hana_needs_only_host_and_port(self):
        url = urls.hana_url(config(host="hana.example.com", port=39013, database=""))
        self.assertEqual(str(url), "hana+hdbcli://auditor:***@hana.example.com:39013")

    def test_snowflake_joins_database_and_schema_and_adds_role_only_when_set(self):
        base = dict(host="xy12345.eu-west-1", port=443, database="AUDITDB", schema="PUBLIC", snowflake_warehouse="AUDIT_WH")
        plain = urls.snowflake_url(config(**base))
        self.assertEqual(plain.database, "AUDITDB/PUBLIC")
        self.assertEqual(dict(plain.query), {"warehouse": "AUDIT_WH"})
        with_role = urls.snowflake_url(config(**base, snowflake_role="AUDITOR"))
        self.assertEqual(dict(with_role.query), {"warehouse": "AUDIT_WH", "role": "AUDITOR"})

    def test_snowflake_key_pair_never_puts_the_key_or_a_password_in_the_url(self):
        url = urls.snowflake_url(config(host="xy", schema="S", snowflake_warehouse="W", snowflake_auth_method="key_pair", password="", snowflake_private_key=pem()))
        self.assertIsNone(url.password)
        self.assertNotIn("PRIVATE", url.render_as_string(hide_password=False))


class ConnectArgTests(unittest.TestCase):
    def test_oracle_sets_a_tcp_connect_timeout(self):
        self.assertEqual(urls.oracle_connect_args(config()), {"tcp_connect_timeout": 8})

    def test_hana_encryption_defaults_on_and_the_timeout_is_in_milliseconds(self):
        on = urls.hana_connect_args(config(sap_hana_encrypt=True))
        self.assertEqual((on["encrypt"], on["sslValidateCertificate"], on["communicationTimeout"]), (True, True, 8000))
        off = urls.hana_connect_args(config(sap_hana_encrypt=False))
        self.assertEqual((off["encrypt"], off["sslValidateCertificate"]), (False, False))

    def test_snowflake_password_mode_passes_no_key(self):
        self.assertEqual(urls.snowflake_connect_args(config(snowflake_auth_method="password")), {"login_timeout": 8})

    def test_snowflake_key_pair_passes_the_key_as_der_pkcs8_bytes(self):
        for passphrase in (None, "pp-123"):
            key_pem = pem(passphrase.encode() if passphrase else None)
            args = urls.snowflake_connect_args(config(snowflake_auth_method="key_pair", snowflake_private_key=key_pem, snowflake_key_passphrase=passphrase))
            der = args["private_key"]
            self.assertIsInstance(der, bytes)
            loaded = serialization.load_der_private_key(der, password=None)  # DER, unencrypted, loadable
            self.assertEqual(loaded.key_size, 2048)

    def test_a_key_that_needs_a_passphrase_it_was_not_given_is_an_error_not_a_silent_failure(self):
        with self.assertRaises(TypeError):
            urls.snowflake_connect_args(config(snowflake_auth_method="key_pair", snowflake_private_key=pem(b"secret"), snowflake_key_passphrase=None))


class ConnectorTests(unittest.TestCase):
    def test_every_engine_is_registered_under_the_name_the_platform_uses(self):
        self.assertEqual(sorted(_REGISTRY), ["mongodb", "mysql", "oracle", "postgresql", "sap_hana", "snowflake", "sql_server"])

    def test_each_new_connector_builds_an_engine_on_the_right_dialect_without_connecting(self):
        cases = {
            "oracle": (config(), "oracle", "SELECT 1 FROM DUAL"),
            "sap_hana": (config(host="h", port=39013, database=""), "hana", "SELECT 1 FROM DUMMY"),
            "snowflake": (config(host="xy", schema="PUBLIC", snowflake_warehouse="WH"), "snowflake", "SELECT 1"),
        }
        for source_type, (cfg, dialect, ping) in cases.items():
            connector = build_connector(source_type, cfg)
            self.assertEqual(connector._engine.dialect.name, dialect, source_type)
            self.assertEqual(connector.ping_sql, ping, source_type)
            connector.close()

    def test_an_unreachable_server_is_reported_as_a_failed_test_not_a_crash(self):
        connector = build_connector("oracle", config(host="127.0.0.1", port=1))  # nothing listens there
        ok, error = connector.test_connection()
        self.assertFalse(ok)
        self.assertTrue(error)
        connector.close()

    def test_the_existing_engines_are_unchanged(self):
        for source_type, prefix in (("postgresql", "postgresql+psycopg://"), ("mysql", "mysql+pymysql://"), ("sql_server", "mssql+pymssql://")):
            connector = build_connector(source_type, config(port=5432, database="app"))
            self.assertTrue(connector.build_url().startswith(prefix))
            self.assertEqual(connector.connect_args(), {})
            self.assertEqual(connector.ping_sql, "SELECT 1")
            connector.close()


class _Recorder:
    """Stands in for a SQLAlchemy connection: records the statements and what was set on the driver connection."""

    def __init__(self, fail_on: str | None = None):
        self.statements: list[str] = []
        self.rolled_back = False
        self.fail_on = fail_on
        self.dbapi = mock.Mock()
        self.connection = mock.Mock(dbapi_connection=self.dbapi)

    def execute(self, statement, *a, **k):
        sql = str(statement)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("the server refused")
        self.statements.append(sql)

    def rollback(self):
        self.rolled_back = True


def connector_on(dialect_name: str) -> SqlAlchemyConnector:
    connector = SqlAlchemyConnector.__new__(SqlAlchemyConnector)
    dialect = mock.Mock()
    dialect.name = dialect_name  # not Mock(name=...): that names the mock, it does not set an attribute
    connector._engine = mock.Mock(dialect=dialect)
    return connector


class ReadGuardTests(unittest.TestCase):
    def test_postgres_and_mysql_keep_their_guards(self):
        pg = _Recorder()
        connector_on("postgresql")._apply_read_guards(pg)
        self.assertEqual(pg.statements, ["SET LOCAL statement_timeout = 15000", "SET TRANSACTION READ ONLY"])
        my = _Recorder()
        connector_on("mysql")._apply_read_guards(my)
        self.assertEqual(my.statements, ["SET SESSION MAX_EXECUTION_TIME = 15000"])

    def test_oracle_gets_a_call_timeout_and_a_read_only_transaction_started_first(self):
        conn = _Recorder()
        connector_on("oracle")._apply_read_guards(conn)
        self.assertEqual(conn.dbapi.call_timeout, 15000)
        self.assertEqual(conn.statements, ["SET TRANSACTION READ ONLY"])

    def test_snowflake_gets_a_statement_timeout(self):
        conn = _Recorder()
        connector_on("snowflake")._apply_read_guards(conn)
        self.assertEqual(conn.statements, ["ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 15"])

    def test_hana_and_other_engines_run_no_guard(self):
        for name in ("hana", "mssql", "sqlite"):
            conn = _Recorder()
            connector_on(name)._apply_read_guards(conn)
            self.assertEqual(conn.statements, [], name)

    def test_a_guard_the_server_refuses_is_skipped_never_fatal(self):
        for name, refuse in (("oracle", "READ ONLY"), ("snowflake", "ALTER SESSION"), ("postgresql", "statement_timeout")):
            conn = _Recorder(fail_on=refuse)
            connector_on(name)._apply_read_guards(conn)  # must not raise
            self.assertTrue(conn.rolled_back, name)


class CompiledSqlTests(unittest.TestCase):
    """The SQL this Gateway sends, compiled by each engine's own dialect, read as that engine would."""

    def measurement_sql(self, dialect, child_type, parent_type):
        captured = []

        class Conn:
            def __init__(self):
                self.dialect = dialect

            def execute(self, statement):
                captured.append(statement)
                raise RuntimeError("captured")

        pair = {
            "child_field_id": "a", "parent_field_id": "b", "child_entity": "orders", "child_column": "customer_id", "child_type": child_type,
            "parent_entity": "customers", "parent_column": "customer_id", "parent_type": parent_type,
        }
        with self.assertRaises(RuntimeError):
            relationships.measure_sql(Conn(), pair, None)
        return str(captured[0].compile(dialect=dialect))

    def test_oracle_casts_to_a_sized_varchar2_because_a_bare_one_is_an_error(self):
        sql = self.measurement_sql(oracle.dialect(), "NUMBER", "VARCHAR2(20)")
        self.assertRegex(sql, r"CAST\(orders\.customer_id AS VARCHAR2\(4000( CHAR)?\)\)")  # sized (the dialect adds CHAR length semantics)
        self.assertNotIn("VARCHAR2)", sql)  # never the bare form Oracle rejects
        self.assertTrue(sql.rstrip().endswith("FROM DUAL"))  # a SELECT with no table needs DUAL on Oracle

    def test_hana_casts_to_a_sized_nvarchar_because_a_bare_one_means_length_one(self):
        from sqlalchemy_hana.dialect import HANAHDBCLIDialect

        sql = self.measurement_sql(HANAHDBCLIDialect(), "INTEGER", "NVARCHAR(20)")
        self.assertIn("AS NVARCHAR(4000)", sql)
        self.assertTrue(sql.rstrip().endswith("FROM DUMMY"))

    def test_the_other_engines_keep_the_unbounded_cast_they_always_had(self):
        from snowflake.sqlalchemy import dialect as snowflake_dialect
        from sqlalchemy.dialects import mssql, mysql, postgresql

        self.assertIn("AS VARCHAR)", self.measurement_sql(postgresql.dialect(), "INTEGER", "TEXT"))
        self.assertIn("AS VARCHAR)", self.measurement_sql(snowflake_dialect(), "NUMBER", "VARCHAR"))
        self.assertNotIn("(4000)", self.measurement_sql(mysql.dialect(), "INT", "TEXT"))
        self.assertNotIn("(4000)", self.measurement_sql(mssql.dialect(), "INT", "TEXT"))

    def test_a_sample_query_is_a_single_bounded_select_on_every_new_engine(self):
        from snowflake.sqlalchemy import dialect as snowflake_dialect
        from sqlalchemy_hana.dialect import HANAHDBCLIDialect

        statement = select(sql_column("id"), sql_column("Name")).select_from(sql_table("employees", schema="AUDIT")).limit(500)
        for dialect, expected in ((oracle.dialect(), "FETCH FIRST"), (HANAHDBCLIDialect(), "LIMIT"), (snowflake_dialect(), "LIMIT")):
            sql = str(statement.compile(dialect=dialect))
            self.assertIn(expected, sql)
            self.assertIn('"Name"', sql)  # a mixed-case name is quoted, an all-lower one is not
            self.assertNotIn(";", sql)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        path = Path(self.dir.name) / "client.db"
        engine = create_engine(f"sqlite:///{path}")
        with engine.begin() as conn:
            conn.execute(text("create table users (user_id integer primary key, email text unique, role_id integer)"))
            conn.execute(text("create table roles (role_id integer primary key, role_name text)"))
            conn.execute(text("create table access (id integer primary key, user_id integer references users(user_id))"))
            conn.execute(text("create index idx_access_user on access(user_id)"))
        engine.dispose()

        class Sqlite(SqlAlchemyConnector):
            def build_url(self):
                return f"sqlite:///{path}"

        self.connector = Sqlite(ConnectorConfig(host="", port=0, database="", username="", password=""))

    def tearDown(self):
        self.connector.close()
        self.dir.cleanup()

    def test_batched_and_per_table_catalogue_reads_discover_exactly_the_same(self):
        batched = self.connector.discover()
        with mock.patch.object(base_module, "_batched", lambda inspector, method, schema: None):
            per_table = self.connector.discover()
        self.assertEqual(batched, per_table)
        access = next(e for e in batched if e["entity_name"] == "access")
        self.assertTrue(access["foreign_keys"])
        self.assertTrue(next(f for f in access["fields"] if f["field_name"] == "user_id")["is_indexed"])

    def test_one_failing_catalogue_call_never_fails_discovery(self):
        from sqlalchemy.engine.reflection import Inspector

        with mock.patch.object(Inspector, "get_multi_foreign_keys", side_effect=RuntimeError("no permission")), mock.patch.object(
            Inspector, "get_foreign_keys", side_effect=RuntimeError("no permission")
        ):
            entities = self.connector.discover()
        self.assertEqual({e["entity_name"] for e in entities}, {"users", "roles", "access"})
        self.assertNotIn("foreign_keys", next(e for e in entities if e["entity_name"] == "access"))  # not reported, not invented


def write_yaml(text_: str) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    handle.write("platform_url: https://platform.example/api/v1\nconnections:\n" + text_)
    handle.close()
    return Path(handle.name)


class ConfigLoadingTests(unittest.TestCase):
    def load(self, entry: str, env: dict | None = None):
        path = write_yaml(entry)
        try:
            with mock.patch.dict(os.environ, env or {}, clear=False):
                return load_settings(path).connections
        finally:
            path.unlink()

    def test_oracle_defaults_to_a_service_name_and_the_standard_port(self):
        [c] = self.load("  - {connection_id: c1, type: oracle, host: db1, database: ORCLPDB1, username: u, password: p}\n")
        cfg = c.connector_config
        self.assertEqual((c.source_type, cfg.port, cfg.oracle_connection_type, cfg.database), ("oracle", 1521, "service_name", "ORCLPDB1"))

    def test_oracle_sid_and_a_password_from_the_environment(self):
        [c] = self.load(
            "  - {connection_id: c1, type: oracle, host: db1, port: 1522, database: ORCL, oracle_connection_type: sid, username: u, password_env: ORA_PW}\n",
            {"ORA_PW": "from-env"},
        )
        self.assertEqual((c.connector_config.oracle_connection_type, c.connector_config.port, c.connector_config.password), ("sid", 1522, "from-env"))

    def test_hana_needs_no_database_and_encryption_defaults_on(self):
        [c] = self.load("  - {connection_id: c1, type: sap_hana, host: h, port: 39013, username: u, password: p}\n")
        self.assertEqual((c.connector_config.database, c.connector_config.sap_hana_encrypt), ("", True))
        [off] = self.load("  - {connection_id: c1, type: sap_hana, host: h, port: 443, username: u, password: p, sap_hana_encrypt: false}\n")
        self.assertFalse(off.connector_config.sap_hana_encrypt)

    def test_snowflake_password_sign_in(self):
        [c] = self.load(
            "  - {connection_id: c1, type: snowflake, host: xy12345.eu-west-1, database: AUDITDB, schema: PUBLIC, snowflake_warehouse: WH, snowflake_role: AUDITOR, username: u, password: p}\n"
        )
        cfg = c.connector_config
        self.assertEqual((cfg.port, cfg.snowflake_auth_method, cfg.snowflake_warehouse, cfg.snowflake_role, cfg.schema), (443, "password", "WH", "AUDITOR", "PUBLIC"))

    def test_snowflake_key_pair_reads_the_key_from_a_file_or_the_environment_and_needs_no_password(self):
        key = pem(b"pp")
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as handle:
            handle.write(key)
        try:
            entry = "  - {connection_id: c1, type: snowflake, host: xy, database: D, schema: S, snowflake_warehouse: WH, username: u, snowflake_auth_method: key_pair, %s, private_key_passphrase_env: SF_PP}\n"
            [from_file] = self.load(entry % f"private_key_file: '{handle.name}'", {"SF_PP": "pp"})
            self.assertEqual((from_file.connector_config.snowflake_private_key, from_file.connector_config.snowflake_key_passphrase, from_file.connector_config.password), (key, "pp", ""))
            [from_env] = self.load(entry % "private_key_env: SF_KEY", {"SF_KEY": key, "SF_PP": "pp"})
            self.assertEqual(from_env.connector_config.snowflake_private_key, key)
        finally:
            os.unlink(handle.name)

    def test_a_missing_setting_is_explained_by_name_not_left_as_a_key_error(self):
        cases = {
            "  - {connection_id: c1, type: oracle, host: db1, username: u, password: p}\n": "database",
            "  - {connection_id: c1, type: snowflake, host: xy, database: D, snowflake_warehouse: WH, username: u, password: p}\n": "schema",
            "  - {connection_id: c1, type: snowflake, host: xy, database: D, schema: S, username: u, password: p}\n": "snowflake_warehouse",
            "  - {connection_id: c1, type: sap_hana, host: h, username: u, password: p}\n": "port",
            "  - {connection_id: c1, type: snowflake, host: xy, database: D, schema: S, snowflake_warehouse: W, username: u, snowflake_auth_method: key_pair}\n": "private_key_file",
            "  - {connection_id: c1, type: oracle, host: db1, database: X, username: u}\n": "password",
        }
        for entry, mentioned in cases.items():
            with self.assertRaises(RuntimeError, msg=entry) as caught:
                self.load(entry)
            self.assertIn(mentioned, str(caught.exception))

    def test_invalid_choices_and_missing_environment_variables_are_refused(self):
        for entry in (
            "  - {connection_id: c1, type: oracle, host: h, database: X, oracle_connection_type: tns, username: u, password: p}\n",
            "  - {connection_id: c1, type: snowflake, host: xy, database: D, schema: S, snowflake_warehouse: W, username: u, password: p, snowflake_auth_method: oauth}\n",
            "  - {connection_id: c1, type: oracle, host: h, database: X, username: u, password_env: NOT_SET_ANYWHERE_123}\n",
            "  - {connection_id: c1, type: snowflake, host: xy, database: D, schema: S, snowflake_warehouse: W, username: u, snowflake_auth_method: key_pair, private_key_env: NOT_SET_ANYWHERE_123}\n",
            "  - {connection_id: c1, type: snowflake, host: xy, database: D, schema: S, snowflake_warehouse: W, username: u, snowflake_auth_method: key_pair, private_key_file: /no/such/file.pem}\n",
        ):
            with self.assertRaises(RuntimeError, msg=entry):
                self.load(entry)

    def test_the_existing_engines_load_exactly_as_before(self):
        [pg, mongo] = self.load(
            "  - {connection_id: c1, type: postgresql, host: h, port: 5432, database: app, username: u, password: p, schema: public}\n"
            "  - {connection_id: c2, type: mongodb, host: cluster.example.net, port: 27017, database: hr, username: u, password: p, mongodb_srv: true}\n"
        )
        self.assertEqual((pg.connector_config.schema, pg.connector_config.oracle_connection_type, pg.connector_config.snowflake_auth_method), ("public", "service_name", "password"))
        self.assertTrue(mongo.connector_config.mongodb_srv)


if __name__ == "__main__":
    unittest.main()
