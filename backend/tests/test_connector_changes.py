"""
Editing a file / SFTP connection goes through the same independent-approval flow as every other
connection edit, with rules of its own: credentials change only through `secrets` (a bare `password` would
overwrite the one encrypted blob of credentials with a plain string and break the connection once
approved), a settings edit is validated exactly like creation, and a change of server drops the recorded
host key and takes the connection out of service until it is tested again. Real HTTP, real roles, the real
database; no network is used.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.models.data_source import DataConnection
from app.services.connectors import config as connector_settings

client = TestClient(app, client=("127.0.0.1", 50000))
PINNED = "SHA256:" + "C" * 43


def bearer(user):
    return {"Authorization": f"Bearer {create_access_token(user_id=user.user_id, organization_id=user.organization_id)}"}


@pytest.fixture
def world(connector_world):
    return connector_world


@pytest.fixture
def approver(connector_approver):
    return connector_approver


def _sftp(world, **config):
    body = {
        "db_type": "sftp", "connection_name": "Payroll drop",
        "config": {"host": "sftp.example.com", "port": 22, "username": "audit", "remote_path": "/exports/payroll_*.csv", "host_key_sha256": PINNED, **config},
        "secrets": {"password": "original-password-1"},
    }
    response = client.post(f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector", json=body, headers=bearer(world.manager))
    assert response.status_code == 201, response.text
    return response.json()["connection_id"]


def _propose(world, connection_id, **fields):
    return client.post(f"/api/v1/connections/{connection_id}/changes/update", json=fields, headers=bearer(world.manager))


def _approve(connection_id, change_id, user):
    return client.post(f"/api/v1/connections/{connection_id}/changes/{change_id}/approve", headers=bearer(user))


def _row(db, connection_id):
    db.expire_all()
    return db.get(DataConnection, uuid.UUID(connection_id))


def _mark_connected(db, connection_id):
    row = _row(db, connection_id)
    row.connection_status = "connected"
    db.commit()


def test_rotating_a_password_replaces_the_credentials_only_once_approved_by_someone_else(db, world, approver):
    cid = _sftp(world)
    _mark_connected(db, cid)
    proposed = _propose(world, cid, secrets={"password": "rotated-password-2"})
    assert proposed.status_code == 202, proposed.text
    change = proposed.json()
    assert "rotated-password-2" not in proposed.text  # the pending secret is only ever stored encrypted
    assert connector_settings.unpack_secrets(_row(db, cid).encrypted_password) == {"password": "original-password-1"}  # nothing live changed yet

    assert _approve(cid, change["change_id"], world.manager).status_code == 403  # the requester cannot approve their own change
    assert _approve(cid, change["change_id"], approver).status_code == 200
    row = _row(db, cid)
    assert connector_settings.unpack_secrets(row.encrypted_password) == {"password": "rotated-password-2"}
    assert row.connector_config["host_key_sha256"] == PINNED  # a new password says nothing about the server's identity
    assert row.connection_status == "pending"  # new credentials are trusted only after a test


def test_a_bare_password_is_refused_because_it_would_break_the_stored_credentials(db, world):
    cid = _sftp(world)
    response = _propose(world, cid, password="oops-this-is-not-a-blob")
    assert response.status_code == 400 and "secrets" in response.json()["detail"]
    assert connector_settings.unpack_secrets(_row(db, cid).encrypted_password) == {"password": "original-password-1"}


def test_moving_to_another_server_forgets_the_recorded_host_key(db, world, approver):
    cid = _sftp(world)
    change = _propose(world, cid, host="sftp2.example.com").json()
    assert _approve(cid, change["change_id"], approver).status_code == 200
    row = _row(db, cid)
    assert row.host == "sftp2.example.com" and "host_key_sha256" not in row.connector_config  # recorded afresh on the next test
    assert row.connector_config["remote_path"] == "/exports/payroll_*.csv" and row.connection_status == "pending"


def test_a_new_server_can_come_with_its_own_pinned_key(db, world, approver):
    cid = _sftp(world)
    fresh = "SHA256:" + "D" * 43
    change = _propose(world, cid, host="sftp2.example.com", connector_config={"host_key_sha256": fresh}).json()
    assert _approve(cid, change["change_id"], approver).status_code == 200
    assert _row(db, cid).connector_config["host_key_sha256"] == fresh


def test_an_edit_is_validated_like_creation(db, world):
    cid = _sftp(world)
    assert _propose(world, cid, host="10.0.0.5").status_code == 400  # an internal address
    assert _propose(world, cid, port=70000).status_code == 400
    assert _propose(world, cid, connector_config={"remote_path": ""}).status_code == 400
    assert _propose(world, cid, connector_config={"unexpected": "setting"}).status_code == 400
    assert _propose(world, cid, connector_config={"auth": "private_key"}).status_code == 400  # the stored password no longer matches
    assert _propose(world, cid, secrets={"password": "a", "api_key": "b"}).status_code == 400
    row = _row(db, cid)
    assert row.host == "sftp.example.com" and row.port == 22  # nothing above changed anything
    assert client.get(f"/api/v1/connections/{cid}/changes", headers=bearer(world.manager)).json() == []


def test_database_only_fields_do_not_apply_to_a_connector(world):
    cid = _sftp(world)
    for field in ({"database_name": "x"}, {"snowflake_warehouse": "x"}, {"mongodb_srv": False}, {"sap_hana_encrypt": False}):
        assert _propose(world, cid, **field).status_code == 400


def test_a_file_connection_can_only_be_renamed(db, world, approver):
    response = client.post(
        f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector", json={"db_type": "file_upload"}, headers=bearer(world.manager)
    )
    cid = response.json()["connection_id"]
    _mark_connected(db, cid)
    assert _propose(world, cid, host="example.com").status_code == 400
    assert _propose(world, cid, connector_config={"a": 1}).status_code == 400
    renamed = _propose(world, cid, connection_name="HR files")
    assert renamed.status_code == 202
    assert _approve(cid, renamed.json()["change_id"], approver).status_code == 200
    row = _row(db, cid)
    assert row.connection_name == "HR files" and row.connection_status == "connected"  # a rename alone does not take it out of service


def test_connector_settings_cannot_be_sent_to_a_database_connection(db, world):
    database = DataConnection(
        data_source_id=world.source.data_source_id, connection_mode="direct", db_type="postgresql", host="db.example.com", port=5432,
        database_name="app", username="u", connection_status="connected",
    )
    db.add(database)
    db.commit()
    for field in ({"connector_config": {"a": 1}}, {"secrets": {"password": "x"}}):
        response = _propose(world, str(database.connection_id), **field)
        assert response.status_code == 400 and "only apply" in response.json()["detail"]


def test_others_cannot_propose_or_approve_a_change(world, approver):
    cid = _sftp(world)
    change = _propose(world, cid, connection_name="renamed").json()
    assert client.post(f"/api/v1/connections/{cid}/changes/update", json={"connection_name": "x"}, headers=bearer(world.read_only)).status_code == 403
    assert client.post(f"/api/v1/connections/{cid}/changes/update", json={"connection_name": "x"}, headers=bearer(world.outsider)).status_code == 403
    assert _approve(cid, change["change_id"], world.read_only).status_code == 403
    assert _approve(cid, change["change_id"], world.outsider).status_code == 403
