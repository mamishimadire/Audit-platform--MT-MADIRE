import types

import pytest

from app.schemas.data_source import DirectConnectionCreate
from app.services.data_source_service import (
    _DIRECT_DRIVER_BY_TYPE,
    _direct_connect_args,
    _direct_engine_url,
)


def _fake_connection(**overrides):
    defaults = dict(
        db_type="oracle",
        oracle_connection_type="service_name",
        username="auditor",
        host="db.example.com",
        port=1521,
        database_name="ORCLPDB1",
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def test_oracle_registered_with_thin_mode_driver():
    assert _DIRECT_DRIVER_BY_TYPE["oracle"] == "oracle+oracledb"


def test_service_name_goes_in_query_string_not_the_path():
    url = _direct_engine_url(_fake_connection(oracle_connection_type="service_name", database_name="ORCLPDB1"), password="x")
    assert url.database is None
    assert url.query.get("service_name") == "ORCLPDB1"
    assert str(url) == "oracle+oracledb://auditor:***@db.example.com:1521?service_name=ORCLPDB1"


def test_sid_goes_in_the_path_like_every_other_engine():
    url = _direct_engine_url(_fake_connection(oracle_connection_type="sid", database_name="ORCL"), password="x")
    assert url.database == "ORCL"
    assert "service_name" not in url.query
    assert str(url) == "oracle+oracledb://auditor:***@db.example.com:1521/ORCL"


def test_special_characters_in_password_do_not_corrupt_the_url():
    url = _direct_engine_url(_fake_connection(), password="p@ss:w/ord%25")
    assert url.password == "p@ss:w/ord%25"
    assert url.host == "db.example.com"
    assert url.username == "auditor"


def test_oracle_connect_args_set_a_tcp_connect_timeout():
    assert _direct_connect_args(_fake_connection()) == {"tcp_connect_timeout": 8}


def test_other_engines_still_use_database_in_the_path():
    url = _direct_engine_url(
        _fake_connection(db_type="postgresql", oracle_connection_type=None, database_name="auditdb"),
        password="x",
    )
    assert url.database == "auditdb"
    assert dict(url.query) == {}


def test_schema_rejects_oracle_without_a_connection_type():
    with pytest.raises(ValueError, match="oracle_connection_type"):
        DirectConnectionCreate(
            db_type="oracle",
            host="db.example.com",
            port=1521,
            database_name="ORCL",
            username="auditor",
            password="x",
        )


def test_schema_accepts_oracle_with_sid():
    payload = DirectConnectionCreate(
        db_type="oracle",
        host="db.example.com",
        port=1521,
        database_name="ORCL",
        username="auditor",
        password="x",
        oracle_connection_type="sid",
    )
    assert payload.oracle_connection_type == "sid"


def test_schema_does_not_require_connection_type_for_other_engines():
    payload = DirectConnectionCreate(
        db_type="postgresql",
        host="db.example.com",
        port=5432,
        database_name="auditdb",
        username="auditor",
        password="x",
    )
    assert payload.oracle_connection_type is None
