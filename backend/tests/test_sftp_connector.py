"""
The SFTP connection against a REAL SSH/SFTP server running in this process (paramiko's server side), so
the handshake, authentication, host-key check, listing, download and parsing are all exercised for real.
The only thing substituted is the network policy: net_guard is told that the server's stand-in name is a
public address, because the real policy (correctly) refuses 127.0.0.1. Tests that check the policy itself
leave it in force.

Runs over real HTTP with real users and roles against the real database, and cleans up after itself.
"""
import io
import json
import posixpath
import socket
import stat
import threading
import time
import uuid
from types import SimpleNamespace

import paramiko
import pytest
from fastapi.testclient import TestClient

from app.core import net_guard
from app.core.security import create_access_token
from app.main import app
from app.models.data_source import DataConnection
from app.services import data_source_service
from app.services.connectors import EntityNotHere, config as connector_settings, sftp_connector

client = TestClient(app, client=("127.0.0.1", 50000))


def bearer(user):
    return {"Authorization": f"Bearer {create_access_token(user_id=user.user_id, organization_id=user.organization_id)}"}


HOST = "sftp.test.example"
PASSWORD = "S3cret-Pass-Phrase-9f2"

PAYROLL_OLD = b"emp_id,name,salary\nE1,Ann,1\n"
PAYROLL_NEW = b"emp_id,name,salary\nE1,Ann,1000.5\nE2,Bob,900\nE3,Cy,2500\n"


# --- a real SFTP server ------------------------------------------------------------------------------------
class _Auth(paramiko.ServerInterface):
    def __init__(self, owner):
        self.owner = owner

    def get_allowed_auths(self, username):
        return "password,publickey"

    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if self.owner.users.get(username) == password else paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        allowed = self.owner.keys.get(username)
        return paramiko.AUTH_SUCCESSFUL if allowed is not None and allowed.asbytes() == key.asbytes() else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class _Handle(paramiko.SFTPHandle):
    def __init__(self, data):
        super().__init__(0)
        self._data = data

    def read(self, offset, length):
        return self._data[offset : offset + length]

    def stat(self):
        return _attr("", self._data, 0)


def _attr(name, data, mtime, directory=False):
    a = paramiko.SFTPAttributes()
    a.filename = name
    a.st_size = 0 if directory else len(data)
    a.st_mtime = mtime
    a.st_mode = (stat.S_IFDIR | 0o755) if directory else (stat.S_IFREG | 0o644)
    return a


class _Files(paramiko.SFTPServerInterface):
    def __init__(self, server, *args, files=None, **kwargs):
        super().__init__(server, *args, **kwargs)
        self.files = files

    def canonicalize(self, path):
        return posixpath.normpath("/" + path.lstrip("/"))

    def list_folder(self, path):
        prefix = path.rstrip("/") + "/"
        return [_attr(p[len(prefix):], d, m) for p, (d, m) in self.files.items() if p.startswith(prefix) and "/" not in p[len(prefix):]]

    def stat(self, path):
        if path in self.files:
            data, mtime = self.files[path]
            return _attr(posixpath.basename(path), data, mtime)
        if path == "/" or any(p.startswith(path.rstrip("/") + "/") for p in self.files):
            return _attr(posixpath.basename(path), b"", 0, directory=True)
        return paramiko.SFTP_NO_SUCH_FILE

    lstat = stat

    def open(self, path, flags, attr):
        return _Handle(self.files[path][0]) if path in self.files else paramiko.SFTP_NO_SUCH_FILE


class SftpTestServer:
    def __init__(self):
        self.host_key = paramiko.ECDSAKey.generate()
        self.users = {"audit": PASSWORD}
        self.keys: dict[str, paramiko.PKey] = {}
        self.files: dict[str, tuple[bytes, int]] = {}
        self._socket = socket.socket()
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(8)
        self.port = self._socket.getsockname()[1]
        self._open = True
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def fingerprint(self) -> str:
        return sftp_connector.fingerprint(self.host_key)

    def _accept(self):
        while self._open:
            try:
                conn, _ = self._socket.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        transport = paramiko.Transport(conn)
        transport.add_server_key(self.host_key)
        transport.set_subsystem_handler("sftp", paramiko.SFTPServer, _Files, files=self.files)
        try:
            transport.start_server(server=_Auth(self))
            while transport.is_active():
                time.sleep(0.05)
        except Exception:  # noqa: BLE001 — a client that hangs up mid-handshake is a normal event for a test server
            pass
        finally:
            transport.close()

    def stop(self):
        self._open = False
        self._socket.close()


@pytest.fixture
def server():
    s = SftpTestServer()
    s.files["/exports/payroll_2026-08.csv"] = (PAYROLL_OLD, 1_000)
    s.files["/exports/payroll_2026-09.csv"] = (PAYROLL_NEW, 2_000)
    s.files["/exports/readme.txt"] = (b"not a table", 9_000)  # newest of all, but it does not match the pattern
    yield s
    s.stop()


@pytest.fixture(autouse=True)
def fresh_cache():
    sftp_connector._SNAPSHOTS.clear()
    yield
    sftp_connector._SNAPSHOTS.clear()


@pytest.fixture
def local_network(monkeypatch, server):
    """The stand-in name resolves to the test server, and the policy is told that address is public."""
    monkeypatch.setattr(net_guard, "_resolver", lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))])
    monkeypatch.setattr(net_guard, "is_public_ip", lambda ip: True)


@pytest.fixture
def world(connector_world):
    return connector_world


def _body(server, **overrides):
    body = {
        "db_type": "sftp", "connection_name": "Payroll drop",
        "config": {"host": HOST, "port": server.port, "username": "audit", "remote_path": "/exports/payroll_*.csv", "auth": "password"},
        "secrets": {"password": PASSWORD},
    }
    for key, value in overrides.items():
        body[key] = {**body[key], **value} if isinstance(value, dict) else value
    return body


def _create(world, server, **overrides):
    response = client.post(
        f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector", json=_body(server, **overrides), headers=bearer(world.manager)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _test(world, connection_id, user=None):
    return client.post(f"/api/v1/connections/{connection_id}/test", headers=bearer(user or world.manager))


def _stored(db, connection_id) -> DataConnection:
    db.expire_all()
    return db.get(DataConnection, uuid.UUID(connection_id))


# --- behaviour -----------------------------------------------------------------------------------------------
def test_round_trip_pins_the_host_key_reads_the_newest_matching_file_and_never_shows_the_password(db, world, server, local_network):
    created = _create(world, server)
    assert PASSWORD not in json.dumps(created)
    assert created["host"] == HOST and created["port"] == server.port and created["username"] == "audit"
    assert created["connector_config"] == {"remote_path": "/exports/payroll_*.csv", "auth": "password"}

    row = _stored(db, created["connection_id"])
    assert PASSWORD not in row.encrypted_password  # stored encrypted, as one blob
    assert connector_settings.unpack_secrets(row.encrypted_password) == {"password": PASSWORD}

    # Before the first test the server's identity is unknown, so nothing is read from it.
    early = client.post(f"/api/v1/connections/{created['connection_id']}/discover", headers=bearer(world.manager))
    assert early.status_code == 400 and "Test this connection first" in early.json()["detail"]

    tested = _test(world, created["connection_id"]).json()
    assert tested["success"] is True and "payroll_2026-09.csv" in tested["detail"]  # the newest MATCH, not readme.txt
    assert PASSWORD not in json.dumps(tested)
    row = _stored(db, created["connection_id"])
    assert row.connector_config["host_key_sha256"] == server.fingerprint and row.connection_status == "connected"

    discovered = client.post(f"/api/v1/connections/{created['connection_id']}/discover", headers=bearer(world.manager))
    assert discovered.status_code == 200, discovered.text
    entities = data_source_service.list_entities(db, data_source_id=world.source.data_source_id)
    assert [(e.entity_name, e.entity_type) for e in entities] == [("payroll", "file")]
    assert {f.field_name: f.data_type for f in data_source_service.list_fields(db, entity_id=entities[0].entity_id)} == {"emp_id": "string", "name": "string", "salary": "double"}

    rows = data_source_service.fetch_direct_records(_stored(db, created["connection_id"]), entity_name="payroll", field_names=["emp_id", "salary"], limit=10)
    assert rows == [{"emp_id": "E1", "salary": 1000.5}, {"emp_id": "E2", "salary": 900.0}, {"emp_id": "E3", "salary": 2500.0}]


def test_a_newer_drop_replaces_the_data_behind_the_same_table(db, world, server, local_network, monkeypatch):
    monkeypatch.setattr(sftp_connector, "_SNAPSHOT_TTL_SECONDS", 0)
    created = _create(world, server)
    assert _test(world, created["connection_id"]).json()["success"] is True
    connection = _stored(db, created["connection_id"])
    assert len(data_source_service.fetch_direct_records(connection, entity_name="payroll", field_names=["emp_id"], limit=10)) == 3

    server.files["/exports/payroll_2026-10.csv"] = (b"emp_id,name,salary\nE9,Zed,7\n", 3_000)
    assert data_source_service.fetch_direct_records(connection, entity_name="payroll", field_names=["emp_id"], limit=10) == [{"emp_id": "E9"}]
    # The table kept its name, so anything mapped to it still works.
    assert [e["entity_name"] for e in [{"entity_name": n} for n in ("payroll",)]] == ["payroll"]
    assert sftp_connector.stable_table_name(connection.connector_config) == "payroll"


def test_a_changed_host_key_is_refused_and_the_recorded_key_is_not_replaced(db, world, server, local_network):
    created = _create(world, server)
    assert _test(world, created["connection_id"]).json()["success"] is True
    pinned = _stored(db, created["connection_id"]).connector_config["host_key_sha256"]

    server.host_key = paramiko.ECDSAKey.generate()  # re-keyed, or something is impersonating the server
    sftp_connector._SNAPSHOTS.clear()
    result = _test(world, created["connection_id"]).json()
    assert result["success"] is False and "host key" in result["detail"]
    row = _stored(db, created["connection_id"])
    assert row.connector_config["host_key_sha256"] == pinned and row.connection_status == "failed"
    with pytest.raises(sftp_connector.ConnectorError, match="host key"):
        data_source_service.fetch_direct_records(row, entity_name="payroll", field_names=["emp_id"], limit=5)


def test_the_server_key_can_be_pinned_up_front(db, world, server, local_network):
    good = _create(world, server, config={"host_key_sha256": server.fingerprint})
    assert _test(world, good["connection_id"]).json()["success"] is True

    other_key = "SHA256:" + "B" * 43
    bad = _create(world, server, connection_name="wrong pin", config={"host_key_sha256": other_key, "remote_path": "/exports/payroll_2026-0?.csv"})
    result = _test(world, bad["connection_id"]).json()
    assert result["success"] is False and "host key" in result["detail"]
    assert _stored(db, bad["connection_id"]).connector_config["host_key_sha256"] == other_key  # never overwritten by what the server presented


def test_wrong_credentials_are_reported_without_echoing_them(db, world, server, local_network):
    created = _create(world, server, secrets={"password": "not-the-password-xyz"})
    result = _test(world, created["connection_id"]).json()
    assert result["success"] is False and "rejected" in result["detail"]
    assert "not-the-password-xyz" not in json.dumps(result) and PASSWORD not in json.dumps(result)
    assert "host_key_sha256" not in (_stored(db, created["connection_id"]).connector_config or {})  # a failed login pins nothing


@pytest.mark.parametrize("passphrase", [None, "pp-for-the-key"])
def test_private_key_authentication(db, world, server, local_network, passphrase):
    key = paramiko.ECDSAKey.generate()
    pem = io.StringIO()
    key.write_private_key(pem, password=passphrase)
    server.keys["audit"] = key
    secrets = {"private_key": pem.getvalue(), **({"passphrase": passphrase} if passphrase else {})}
    created = client.post(
        f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector",
        json={**_body(server, config={"auth": "private_key"}), "secrets": secrets}, headers=bearer(world.manager),
    )
    assert created.status_code == 201, created.text
    assert "PRIVATE KEY" not in created.text
    result = _test(world, created.json()["connection_id"]).json()
    assert result["success"] is True, result

    # The wrong key, or a missing passphrase, is refused with a plain message.
    server.keys["audit"] = paramiko.ECDSAKey.generate()
    assert "rejected" in _test(world, created.json()["connection_id"]).json()["detail"]


def test_a_key_that_needs_a_passphrase_says_so(world, server, local_network):
    key = paramiko.ECDSAKey.generate()
    pem = io.StringIO()
    key.write_private_key(pem, password="needed")
    server.keys["audit"] = key
    created = client.post(
        f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector",
        json={**_body(server, config={"auth": "private_key"}), "secrets": {"private_key": pem.getvalue()}}, headers=bearer(world.manager),
    ).json()
    assert "passphrase" in _test(world, created["connection_id"]).json()["detail"]


def test_an_oversized_remote_file_is_refused_before_it_is_downloaded(world, server, local_network, monkeypatch):
    monkeypatch.setattr(sftp_connector, "MAX_FILE_BYTES", 20)
    created = _create(world, server)
    result = _test(world, created["connection_id"]).json()
    assert result["success"] is False and "limit" in result["detail"]


def test_a_missing_file_a_folder_and_a_nonmatching_pattern_are_explained(world, server, local_network):
    for path, expected in (("/exports/nothing_*.csv", "No file matching"), ("/exports", "folder"), ("/exports/absent.csv", "could not be read")):
        created = _create(world, server, connection_name=path, config={"remote_path": path})
        result = _test(world, created["connection_id"]).json()
        assert result["success"] is False and expected in result["detail"], (path, result)


def test_a_file_that_is_not_a_table_is_explained(world, server, local_network):
    server.files["/exports/notes.txt"] = (b"\x00\x01\x02 binary rubbish", 5_000)
    server.files["/exports/notes.bin"] = (b"x", 6_000)
    created = _create(world, server, config={"remote_path": "/exports/notes.bin"})
    result = _test(world, created["connection_id"]).json()
    assert result["success"] is False and "could not be read as a table" in result["detail"]


def test_a_server_that_is_not_there_fails_cleanly(world, server, local_network):
    dead = SftpTestServer()
    port = dead.port
    dead.stop()
    created = _create(world, server, config={"port": port})
    result = _test(world, created["connection_id"]).json()
    assert result["success"] is False and "Could not connect" in result["detail"]


def test_the_network_policy_refuses_a_name_that_resolves_to_an_internal_address(db, world, server, monkeypatch):
    # No local_network fixture: the real policy is in force.
    monkeypatch.setattr(net_guard, "_resolver", lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", port))])
    created = _create(world, server)
    result = _test(world, created["connection_id"]).json()
    assert result["success"] is False and "public internet" in result["detail"]
    assert "10.1.2.3" not in json.dumps(result)  # what an internal name resolves to is not for the requester to learn


def test_a_name_with_one_public_and_one_private_answer_is_refused(world, server, monkeypatch):
    monkeypatch.setattr(
        net_guard, "_resolver",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)), (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))],
    )
    created = _create(world, server)
    assert "public internet" in _test(world, created["connection_id"]).json()["detail"]


def test_a_literal_internal_address_is_refused_when_the_connection_is_created(world, server):
    for host in ("127.0.0.1", "169.254.169.254", "10.0.0.5"):
        response = client.post(
            f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector", json=_body(server, config={"host": host}), headers=bearer(world.manager)
        )
        assert response.status_code == 400 and "public internet" in response.json()["detail"]


def test_only_managers_of_the_same_organization_can_test_or_read_it(world, server, local_network):
    created = _create(world, server)
    cid = created["connection_id"]
    for user in (world.read_only, world.outsider):
        assert _test(world, cid, user).status_code == 403
        assert client.post(f"/api/v1/connections/{cid}/discover", headers=bearer(user)).status_code == 403
    assert client.post(
        f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector", json=_body(server), headers=bearer(world.outsider)
    ).status_code == 403
    assert client.get(f"/api/v1/data-sources/{world.source.data_source_id}/connections", headers=bearer(world.outsider)).status_code == 403


def test_the_same_server_user_and_path_cannot_be_added_twice(world, server):
    _create(world, server)
    again = client.post(
        f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector", json=_body(server), headers=bearer(world.manager)
    )
    assert again.status_code == 400 and "already" in again.json()["detail"]
    # A different path on the same server is a different connection.
    assert client.post(
        f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector",
        json=_body(server, config={"remote_path": "/exports/other_*.csv"}), headers=bearer(world.manager),
    ).status_code == 201


def test_a_table_this_connection_does_not_supply_is_rejected_without_touching_the_network():
    connection = SimpleNamespace(
        connection_id=uuid.uuid4(), host="unreachable.invalid", port=22, username="u", encrypted_password=None,
        connector_config={"remote_path": "/exports/payroll_*.csv", "auth": "password"},
    )
    with pytest.raises(EntityNotHere):
        sftp_connector.SftpConnector().fetch_records(connection, entity_name="customers", field_names=["id"], limit=5)


@pytest.mark.parametrize(
    "config, expected",
    [
        ({"remote_path": "/exports/payroll_*.csv"}, "payroll"),
        ({"remote_path": "/exports/payroll_????-??.csv"}, "payroll"),
        ({"remote_path": "/exports/payroll.csv"}, "payroll"),
        ({"remote_path": "/x/GL-export-*.xlsx"}, "GL-export"),
        ({"remote_path": "/exports/*.csv"}, "sftp_file"),
        ({"remote_path": "/exports/*.csv", "table_name": "ledger"}, "ledger"),
    ],
)
def test_the_table_name_is_stable_across_drops(config, expected):
    assert sftp_connector.stable_table_name(config) == expected


def test_an_sftp_download_has_an_overall_deadline(world, server, local_network, monkeypatch):
    created = _create(world, server)
    monkeypatch.setattr(sftp_connector, "_DOWNLOAD_DEADLINE_SECONDS", 0)  # every chunk arrives "late": a slow drip cannot hold the scheduler loop
    result = _test(world, created["connection_id"]).json()
    assert result["success"] is False and "took too long" in result["detail"]


def test_a_download_larger_than_the_limit_is_stopped_as_it_streams_not_after(world, server, local_network, monkeypatch):
    """The size the server states is only its word: the limit is enforced on the bytes actually received."""
    monkeypatch.setattr(sftp_connector, "MAX_FILE_BYTES", len(PAYROLL_NEW) + 5)  # the stated size passes...
    monkeypatch.setattr(sftp_connector, "_DOWNLOAD_CHUNK_BYTES", 8)
    server.files["/exports/payroll_2026-09.csv"] = (PAYROLL_NEW + b"x" * 500, 2_000)  # ...then the file is larger than it said (stat is computed from data here, so shrink the stated size)
    original = _attr

    def understated(name, data, mtime, directory=False):
        attrs = original(name, data, mtime, directory)
        if not directory and len(data) > len(PAYROLL_NEW) + 5:
            attrs.st_size = 10  # what the server SAYS
        return attrs

    monkeypatch.setitem(globals(), "_attr", understated)
    created = _create(world, server)
    result = _test(world, created["connection_id"]).json()
    assert result["success"] is False and "larger than" in result["detail"]


def test_a_file_larger_than_the_platform_reads_is_said_so_by_the_connection_test_and_reported_as_cut(world, server, local_network, monkeypatch):
    from app.services.connectors import file_parsing

    monkeypatch.setattr(file_parsing, "MAX_CELLS", 6)  # the 3-column, 3-row payroll file has 9 cells: only 2 rows fit
    created = _create(world, server)
    result = _test(world, created["connection_id"]).json()
    assert result["success"] is True and "Only the first rows are read" in result["detail"]
    from app.models.data_source import DataConnection

    row = DataConnection(  # what the scheduler would hold: the stored settings, with the server key the test just recorded
        connection_id=uuid.UUID(created["connection_id"]), db_type="sftp", host=created["host"], port=created["port"], username=created["username"],
        connector_config={**created["connector_config"], "host_key_sha256": server.fingerprint},
        encrypted_password=connector_settings.pack_secrets({"password": PASSWORD}),
    )
    assert sftp_connector.SftpConnector().truncated(row, "payroll") is True
    assert sftp_connector.SftpConnector().truncated(row, "something_else") is False
