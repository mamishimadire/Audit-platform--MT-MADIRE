"""
The reach policy for the eight direct database connections that existed before the file / SFTP / API ones:
the platform dials only the public internet. A host that resolves to a private, loopback, link-local or
metadata address is refused before any driver is asked to connect, so a database connection cannot be used
to probe the platform's own network. No database or network is needed: DNS answers are simulated.
"""
import socket
from types import SimpleNamespace

import pytest

from app.core import net_guard
from app.core.crypto import encrypt_secret
from app.services import data_source_service, mongo_connector
from app.services.connectors.config import ConfigError, refuse_internal_literal


def answer(*ips):
    def resolver(host, port):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    return resolver


def sql_connection(db_type="postgresql", host="db.customer.example", port=5432, **extra):
    return SimpleNamespace(
        db_type=db_type, host=host, port=port, database_name="app", username="u", encrypted_password=encrypt_secret("pw"),
        oracle_connection_type="sid", sap_hana_encrypt=True, snowflake_warehouse="wh", snowflake_schema="public", snowflake_role=None,
        snowflake_auth_method="password", encrypted_snowflake_key_passphrase=None, **extra,
    )


@pytest.fixture
def no_engine(monkeypatch):
    """Nothing may be built (let alone connected) when the destination is refused."""
    built = []
    monkeypatch.setattr(data_source_service, "create_engine", lambda *a, **k: built.append(a) or SimpleNamespace(dispose=lambda: None))
    return built


@pytest.mark.parametrize("db_type", ["postgresql", "mysql", "mssql", "oracle", "sap_hana", "snowflake"])
@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.16.5.5", "192.168.0.9", "169.254.169.254", "100.64.0.1", "::1", "fd00:ec2::254", "fe80::1"])
def test_a_host_that_resolves_to_a_non_public_address_is_refused_before_any_engine_is_built(monkeypatch, no_engine, db_type, ip):
    monkeypatch.setattr(net_guard, "_resolver", answer(ip))
    with pytest.raises(ValueError, match="public internet") as caught:
        data_source_service._build_direct_engine(sql_connection(db_type))
    assert ip not in str(caught.value) and no_engine == []  # what an internal name resolves to is not for the requester to learn


def test_one_internal_answer_among_public_ones_refuses_the_whole_name(monkeypatch, no_engine):
    monkeypatch.setattr(net_guard, "_resolver", answer("93.184.216.34", "127.0.0.1"))
    with pytest.raises(ValueError, match="public internet"):
        data_source_service._build_direct_engine(sql_connection())
    assert no_engine == []


@pytest.mark.parametrize("db_type", ["postgresql", "mysql", "mssql", "oracle", "sap_hana"])
def test_a_public_host_builds_the_engine_exactly_as_before(monkeypatch, no_engine, db_type):
    monkeypatch.setattr(net_guard, "_resolver", answer("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"))
    data_source_service._build_direct_engine(sql_connection(db_type))
    assert len(no_engine) == 1
    assert "db.customer.example" in str(no_engine[0][0])  # the name (not an address) stays in the URL: TLS and virtual hosting keep working


def test_the_port_that_is_vetted_is_the_port_that_is_dialled(monkeypatch):
    seen = []
    monkeypatch.setattr(net_guard, "_resolver", lambda host, port: seen.append((host, port)) or answer("93.184.216.34")(host, port))
    monkeypatch.setattr(data_source_service, "create_engine", lambda *a, **k: None)
    data_source_service._build_direct_engine(sql_connection(port=6543))
    data_source_service._build_direct_engine(sql_connection("mysql", port=None))
    assert seen == [("db.customer.example", 6543), ("db.customer.example", 3306)]  # an unset port falls back to the engine's default


def test_snowflake_is_vetted_at_the_address_the_driver_will_really_dial(monkeypatch, no_engine):
    seen = []
    monkeypatch.setattr(net_guard, "_resolver", lambda host, port: seen.append(host) or answer("93.184.216.34")(host, port))
    data_source_service._build_direct_engine(sql_connection("snowflake", host="xy12345.eu-west-1", port=None))
    data_source_service._build_direct_engine(sql_connection("snowflake", host="acme-prod.snowflakecomputing.com", port=None))
    assert seen == ["xy12345.eu-west-1.snowflakecomputing.com", "acme-prod.snowflakecomputing.com"]


@pytest.mark.parametrize("account", ["evil.example.com/x", "a b", "", "../x", "acct;drop", "a" * 200, "acct@host"])
def test_a_snowflake_account_cannot_name_anything_but_an_account(monkeypatch, no_engine, account):
    monkeypatch.setattr(net_guard, "_resolver", answer("93.184.216.34"))
    with pytest.raises(ValueError, match="account identifier"):
        data_source_service._build_direct_engine(sql_connection("snowflake", host=account, port=None))
    assert no_engine == []


def test_a_name_that_does_not_resolve_is_refused_with_a_plain_message(monkeypatch, no_engine):
    def nxdomain(host, port):
        raise socket.gaierror("nope")

    monkeypatch.setattr(net_guard, "_resolver", nxdomain)
    with pytest.raises(ValueError, match="could not be resolved"):
        data_source_service._build_direct_engine(sql_connection())


def test_test_connection_reports_a_refused_destination_to_the_user_and_records_the_failure(monkeypatch, no_engine):
    monkeypatch.setattr(net_guard, "_resolver", answer("10.0.0.7"))
    recorded = []
    monkeypatch.setattr(data_source_service, "record_connection_test_result", lambda db, *, connection, success: recorded.append(success))
    ok, detail = data_source_service.test_direct_connection(None, connection=sql_connection())
    assert ok is False and "public internet" in detail and recorded == [False] and no_engine == []


# --- MongoDB -----------------------------------------------------------------------------------------------------------
def mongo_connection(host, *, srv, port=27017):
    return SimpleNamespace(host=host, port=port, mongodb_srv=srv, database_name="app", username="u", encrypted_password=encrypt_secret("pw"))


@pytest.fixture
def no_client(monkeypatch):
    made = []
    monkeypatch.setattr(mongo_connector, "MongoClient", lambda *a, **k: made.append(a) or SimpleNamespace(close=lambda: None))
    return made


class _Target:
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return self.name + "."  # DNS names come back fully qualified, with the trailing dot


def fake_srv(monkeypatch, targets):
    import dns.resolver

    def resolve(name, kind, lifetime=None):
        assert name.startswith("_mongodb._tcp.") and kind == "SRV"
        return [SimpleNamespace(target=_Target(t), port=p) for t, p in targets]

    monkeypatch.setattr(dns.resolver, "resolve", resolve)


def test_a_mongodb_cluster_whose_srv_records_point_inside_is_refused(monkeypatch, no_client):
    fake_srv(monkeypatch, [("shard-00.evil.example", 27017)])
    monkeypatch.setattr(net_guard, "_resolver", answer("10.0.0.9"))
    with pytest.raises(ValueError, match="public internet"):
        mongo_connector._build_mongo_client(mongo_connection("cluster.evil.example", srv=True), password="pw")
    assert no_client == []


def test_a_public_srv_cluster_and_a_public_plain_host_are_accepted(monkeypatch, no_client):
    fake_srv(monkeypatch, [("shard-00.atlas.example", 27017), ("shard-01.atlas.example", 27017)])
    monkeypatch.setattr(net_guard, "_resolver", answer("93.184.216.34"))
    mongo_connector._build_mongo_client(mongo_connection("cluster.atlas.example", srv=True), password="pw")
    mongo_connector._build_mongo_client(mongo_connection("mongo.customer.example", srv=False), password="pw")
    assert len(no_client) == 2


def test_a_plain_mongodb_host_that_resolves_inside_is_refused(monkeypatch, no_client):
    monkeypatch.setattr(net_guard, "_resolver", answer("127.0.0.1"))
    with pytest.raises(ValueError, match="public internet"):
        mongo_connector._build_mongo_client(mongo_connection("mongo.evil.example", srv=False), password="pw")
    assert no_client == []


def test_an_srv_address_that_cannot_be_looked_up_is_refused_cleanly(monkeypatch, no_client):
    import dns.resolver

    def broken(*a, **k):
        raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(dns.resolver, "resolve", broken)
    with pytest.raises(ValueError, match="could not be resolved"):
        mongo_connector._build_mongo_client(mongo_connection("nothing.example", srv=True), password="pw")
    assert no_client == []


def test_test_mongo_connection_shows_the_refusal_instead_of_a_generic_failure(monkeypatch, no_client):
    monkeypatch.setattr(net_guard, "_resolver", answer("169.254.169.254"))
    ok, detail = mongo_connector.test_mongo_connection(mongo_connection("mongo.evil.example", srv=False))
    assert ok is False and "public internet" in detail


# --- creation and editing --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "[fd00:ec2::254]", "100.64.0.1", " 10.0.0.5 "])
def test_a_literal_internal_address_is_refused_when_a_connection_is_created_or_edited(host):
    with pytest.raises(ConfigError, match="public internet"):
        refuse_internal_literal(host)


@pytest.mark.parametrize("host", [None, "", "db.customer.example", "93.184.216.34", "xy12345.eu-west-1", "cluster0.abcde.mongodb.net", "my_db.example.com", "2606:2800:220:1:248:1893:25c8:1946"])
def test_names_and_public_addresses_are_left_alone(host):
    refuse_internal_literal(host)  # names are vetted, address by address, when the connection is used
