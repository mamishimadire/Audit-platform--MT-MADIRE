import types

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.crypto import encrypt_secret
from app.schemas.data_source import DirectConnectionCreate
from app.services.data_source_service import (
    _DIRECT_DRIVER_BY_TYPE,
    _direct_connect_args,
    _direct_engine_url,
)


def _test_pem_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8, encryption_algorithm=serialization.NoEncryption()
    ).decode()


def _fake_connection(**overrides):
    defaults = dict(
        db_type="snowflake",
        username="auditor",
        host="xy12345.us-east-1",
        database_name="AUDITDB",
        snowflake_schema="PUBLIC",
        snowflake_warehouse="AUDIT_WH",
        snowflake_role=None,
        snowflake_auth_method="password",
        encrypted_password=None,
        encrypted_snowflake_key_passphrase=None,
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def test_snowflake_registered_with_its_own_dialect():
    assert _DIRECT_DRIVER_BY_TYPE["snowflake"] == "snowflake"


def test_password_mode_url_has_database_and_schema_joined_by_slash():
    url = _direct_engine_url(_fake_connection(), password="x")
    assert url.database == "AUDITDB/PUBLIC"
    assert url.password == "x"
    assert url.query.get("warehouse") == "AUDIT_WH"


def test_role_is_included_only_when_set():
    with_role = _direct_engine_url(_fake_connection(snowflake_role="AUDITOR_ROLE"), password="x")
    assert with_role.query.get("role") == "AUDITOR_ROLE"
    without_role = _direct_engine_url(_fake_connection(snowflake_role=None), password="x")
    assert "role" not in without_role.query


def test_key_pair_mode_url_has_no_password():
    url = _direct_engine_url(_fake_connection(snowflake_auth_method="key_pair"), password="some-pem-content")
    assert url.password is None


def test_password_mode_connect_args_has_no_private_key():
    args = _direct_connect_args(_fake_connection(snowflake_auth_method="password"))
    assert "private_key" not in args
    assert args["login_timeout"] == 8


def test_key_pair_mode_connect_args_derives_private_key_bytes():
    pem = _test_pem_key()
    connection = _fake_connection(snowflake_auth_method="key_pair", encrypted_password=encrypt_secret(pem))
    args = _direct_connect_args(connection)
    assert isinstance(args["private_key"], bytes)
    # DER/PKCS8 bytes should round-trip back into a usable private key.
    loaded = serialization.load_der_private_key(args["private_key"], password=None)
    assert loaded is not None


def test_key_pair_mode_respects_a_passphrase_protected_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(b"correct-horse"),
    ).decode()
    connection = _fake_connection(
        snowflake_auth_method="key_pair",
        encrypted_password=encrypt_secret(pem),
        encrypted_snowflake_key_passphrase=encrypt_secret("correct-horse"),
    )
    args = _direct_connect_args(connection)
    assert isinstance(args["private_key"], bytes)


def test_schema_requires_warehouse_database_and_schema_for_snowflake():
    with pytest.raises(ValueError, match="snowflake_warehouse"):
        DirectConnectionCreate(db_type="snowflake", host="xy12345", username="auditor", password="x", database_name="AUDITDB", snowflake_schema="PUBLIC")


def test_schema_does_not_require_a_port_for_snowflake():
    payload = DirectConnectionCreate(
        db_type="snowflake", host="xy12345", username="auditor", password="x",
        database_name="AUDITDB", snowflake_schema="PUBLIC", snowflake_warehouse="AUDIT_WH",
    )
    assert payload.port is None


def test_schema_still_requires_a_port_for_other_engines():
    with pytest.raises(ValueError, match="port is required"):
        DirectConnectionCreate(db_type="postgresql", host="db.example.com", username="auditor", password="x", database_name="auditdb")


def test_schema_defaults_to_password_auth():
    payload = DirectConnectionCreate(
        db_type="snowflake", host="xy12345", username="auditor", password="x",
        database_name="AUDITDB", snowflake_schema="PUBLIC", snowflake_warehouse="AUDIT_WH",
    )
    assert payload.snowflake_auth_method == "password"
