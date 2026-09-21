"""
Oracle, SAP HANA and Snowflake are reached in two places: by the platform itself (data_source_service, for a
database that is reachable from the internet) and by the Gateway (for one that is not). The two must build the
same connection from the same settings and send the same SQL. The Gateway is deployed independently and cannot
import the backend, so the shared logic is duplicated on purpose; THIS file loads the Gateway's copies by path and
fails if either side changes without the other.

No database and no server: it compares what each side would do.
"""
import importlib.util
import types
from pathlib import Path
from unittest import mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.dialects import oracle

from app.services import data_source_service as platform

_GATEWAY = Path(__file__).resolve().parents[2] / "gateway" / "gateway"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


urls = _load("gateway_urls_under_test", _GATEWAY / "connectors" / "urls.py")
gateway_relationships = _load("gateway_relationships_parity", _GATEWAY / "relationships.py")


def _pem(passphrase: bytes | None = None) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    protection = serialization.BestAvailableEncryption(passphrase) if passphrase else serialization.NoEncryption()
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, protection).decode()


def _both(db_type, **s):
    """The same settings, expressed the way each side names them."""
    gateway = types.SimpleNamespace(
        host=s.get("host", "db.example.com"), port=s.get("port", 1521), database=s.get("database", "SVC"), username="auditor",
        password=s.get("password", "p@ss:w/ord%25"), schema=s.get("schema"),
        oracle_connection_type=s.get("oracle_connection_type", "service_name"), sap_hana_encrypt=s.get("sap_hana_encrypt", True),
        snowflake_warehouse=s.get("snowflake_warehouse"), snowflake_role=s.get("snowflake_role"),
        snowflake_auth_method=s.get("snowflake_auth_method", "password"),
        snowflake_private_key=s.get("snowflake_private_key"), snowflake_key_passphrase=s.get("snowflake_key_passphrase"),
    )
    encrypted = mock.Mock()
    with mock.patch.object(platform, "decrypt_secret", lambda token: {"pw": gateway.snowflake_private_key or "", "pp": gateway.snowflake_key_passphrase or ""}.get(token, token)):
        connection = types.SimpleNamespace(
            db_type=db_type, host=gateway.host, port=gateway.port, database_name=gateway.database, username=gateway.username,
            oracle_connection_type=gateway.oracle_connection_type, sap_hana_encrypt=gateway.sap_hana_encrypt,
            snowflake_warehouse=gateway.snowflake_warehouse, snowflake_schema=gateway.schema, snowflake_role=gateway.snowflake_role,
            snowflake_auth_method=gateway.snowflake_auth_method,
            encrypted_password="pw", encrypted_snowflake_key_passphrase="pp" if gateway.snowflake_key_passphrase else None,
        )
        url = platform._direct_engine_url(connection, password=gateway.password if gateway.snowflake_auth_method == "password" else gateway.snowflake_private_key)
        args = platform._direct_connect_args(connection)
    del encrypted
    return gateway, url, args


def _same_url(a, b):
    assert a.render_as_string(hide_password=False) == b.render_as_string(hide_password=False)
    assert (a.drivername, a.username, a.password, a.host, a.port, a.database, dict(a.query)) == (b.drivername, b.username, b.password, b.host, b.port, b.database, dict(b.query))


@pytest.mark.parametrize("kind, database", [("service_name", "ORCLPDB1"), ("sid", "ORCL")])
@pytest.mark.parametrize("password", ["plain", "p@ss:w/ord%25", "ü ñ 密码"])
def test_oracle_urls_and_arguments_are_identical(kind, database, password):
    gateway, platform_url, platform_args = _both("oracle", oracle_connection_type=kind, database=database, password=password)
    _same_url(urls.oracle_url(gateway), platform_url)
    assert urls.oracle_connect_args(gateway) == platform_args


@pytest.mark.parametrize("encrypt", [True, False])
def test_hana_urls_and_arguments_are_identical(encrypt):
    gateway, platform_url, platform_args = _both("sap_hana", port=39013, database=None, sap_hana_encrypt=encrypt)
    gateway.database = ""
    _same_url(urls.hana_url(gateway), platform_url)
    assert urls.hana_connect_args(gateway) == platform_args


@pytest.mark.parametrize("role", [None, "AUDITOR"])
def test_snowflake_password_urls_and_arguments_are_identical(role):
    gateway, platform_url, platform_args = _both(
        "snowflake", host="xy12345.eu-west-1", port=443, database="AUDITDB", schema="PUBLIC", snowflake_warehouse="AUDIT_WH", snowflake_role=role,
    )
    _same_url(urls.snowflake_url(gateway), platform_url)
    assert urls.snowflake_connect_args(gateway) == platform_args == {"login_timeout": 8}


@pytest.mark.parametrize("passphrase", [None, "a-passphrase"])
def test_snowflake_key_pair_urls_and_arguments_are_identical_including_the_key_bytes(passphrase):
    gateway, platform_url, platform_args = _both(
        "snowflake", host="xy", port=443, database="D", schema="S", snowflake_warehouse="W", snowflake_auth_method="key_pair",
        snowflake_private_key=_pem(passphrase.encode() if passphrase else None), snowflake_key_passphrase=passphrase,
    )
    _same_url(urls.snowflake_url(gateway), platform_url)
    assert platform_url.password is None
    gateway_args = urls.snowflake_connect_args(gateway)
    assert gateway_args == platform_args and isinstance(gateway_args["private_key"], bytes)  # the same DER bytes, from the same key


def test_the_timeouts_agree():
    assert urls.CONNECT_TIMEOUT_SECONDS == platform._CONNECT_TIMEOUT_SECONDS


# --- the same SQL ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("dialect_name", ["oracle", "hana", "snowflake", "postgresql", "mysql", "mssql", "sqlite"])
def test_the_text_cast_used_to_compare_differently_typed_keys_is_identical(dialect_name):
    a, b = platform._text_cast_type(dialect_name), gateway_relationships._text_cast_type(dialect_name)
    assert type(a) is type(b) or a is b  # String (the class) or String(4000) (an instance): the same on both sides
    if not isinstance(a, type):
        assert (a.length, type(a)) == (b.length, type(b))


def test_oracle_and_hana_cast_to_a_sized_string_and_the_others_do_not():
    from sqlalchemy import String

    for name in ("oracle", "hana"):
        assert platform._text_cast_type(name).length == 4000
    for name in ("postgresql", "mysql", "mssql", "snowflake", "sqlite"):
        assert platform._text_cast_type(name) is String


# --- the same guards -----------------------------------------------------------------------------------------------------------------
class _Recorder:
    def __init__(self):
        self.statements: list[str] = []
        self.dbapi = types.SimpleNamespace(call_timeout=None)  # plain objects, so "never set" compares equal (None) on both sides
        self.connection = types.SimpleNamespace(dbapi_connection=self.dbapi)
        self.rolled_back = False

    def execute(self, statement, *a, **k):
        self.statements.append(str(statement))

    def rollback(self):
        self.rolled_back = True


guards = _load("gateway_guards_under_test", _GATEWAY / "connectors" / "guards.py")


@pytest.mark.parametrize("dialect_name", ["postgresql", "mysql", "oracle", "snowflake", "hana", "mssql"])
def test_the_read_guards_are_identical_on_both_sides(dialect_name):
    on_platform, on_gateway = _Recorder(), _Recorder()
    platform._apply_read_guards(on_platform, dialect_name)
    guards.apply_read_guards(on_gateway, dialect_name)
    assert on_platform.statements == on_gateway.statements
    assert on_platform.dbapi.call_timeout == on_gateway.dbapi.call_timeout
    assert guards.PROFILE_STATEMENT_TIMEOUT_MS == platform._PROFILE_STATEMENT_TIMEOUT_MS
    if dialect_name == "oracle":
        assert on_platform.dbapi.call_timeout == 15000 and on_platform.statements == ["SET TRANSACTION READ ONLY"]
    if dialect_name == "snowflake":
        assert on_platform.statements == ["ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 15"]
    if dialect_name in ("hana", "mssql"):
        assert on_platform.statements == []


def test_a_guard_the_server_refuses_is_skipped_on_the_platform_too():
    conn = _Recorder()
    conn.execute = mock.Mock(side_effect=RuntimeError("refused"))
    platform._apply_read_guards(conn, "oracle")  # must not raise
    assert conn.rolled_back


def test_lob_columns_are_left_out_of_sampling_on_both_sides():
    profiling = _load("gateway_profiling_parity", _GATEWAY / "profiling.py")
    assert profiling._BINARY_TYPE_MARKERS == platform._BINARY_TYPE_MARKERS
    for declared in ("CLOB", "NCLOB", "BLOB", "BFILE", "LONG RAW", "bytea"):
        assert any(m in declared.lower() for m in platform._BINARY_TYPE_MARKERS), declared
    for declared in ("VARCHAR2(20)", "NUMBER", "NVARCHAR(50)", "DATE", "TIMESTAMP", "VARIANT"):
        assert not any(m in declared.lower() for m in platform._BINARY_TYPE_MARKERS), declared


def test_the_oracle_dialect_the_platform_uses_renders_the_sized_cast():
    """A real dialect, not a stand-in: what the platform sends Oracle for a mixed-type relationship check."""
    captured = []

    class Conn:
        dialect = oracle.dialect()

        def execute(self, statement):
            captured.append(statement)
            raise RuntimeError("captured")

    from app.core.relationship_inference import Candidate, ColumnRef

    candidate = Candidate(
        child=ColumnRef("f1", "e1", "orders", "customer_id", "NUMBER"), parent=ColumnRef("f2", "e2", "customers", "customer_id", "VARCHAR2(20)"),
        name_affinity=1.0, parent_unique=True,
    )
    with pytest.raises(RuntimeError):
        platform._measure_sql(Conn(), candidate)
    sql = str(captured[0].compile(dialect=oracle.dialect()))
    assert "VARCHAR2(4000" in sql and "VARCHAR2)" not in sql and sql.rstrip().endswith("FROM DUAL")
