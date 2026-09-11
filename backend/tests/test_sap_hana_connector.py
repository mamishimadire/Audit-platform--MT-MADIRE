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
        db_type="sap_hana",
        oracle_connection_type=None,
        username="auditor",
        host="hana.example.com",
        port=39013,
        database_name=None,
        sap_hana_encrypt=True,
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def test_sap_hana_registered_with_hdbcli_driver():
    assert _DIRECT_DRIVER_BY_TYPE["sap_hana"] == "hana+hdbcli"


def test_url_has_no_database_segment_for_a_tenant_connection():
    url = _direct_engine_url(_fake_connection(), password="x")
    assert url.database is None
    assert dict(url.query) == {}
    assert str(url) == "hana+hdbcli://auditor:***@hana.example.com:39013"


def test_special_characters_in_password_do_not_corrupt_the_url():
    url = _direct_engine_url(_fake_connection(), password="p@ss:w/ord%25")
    assert url.password == "p@ss:w/ord%25"
    assert url.host == "hana.example.com"
    assert url.username == "auditor"


def test_encryption_defaults_on_and_maps_to_hdbcli_kwargs():
    args = _direct_connect_args(_fake_connection(sap_hana_encrypt=True))
    assert args["encrypt"] is True
    assert args["sslValidateCertificate"] is True
    assert args["communicationTimeout"] == 8000  # milliseconds, not seconds


def test_encryption_can_be_turned_off():
    args = _direct_connect_args(_fake_connection(sap_hana_encrypt=False))
    assert args["encrypt"] is False
    assert args["sslValidateCertificate"] is False


def test_schema_defaults_encryption_to_true():
    payload = DirectConnectionCreate(
        db_type="sap_hana", host="hana.example.com", port=39013, username="auditor", password="x",
    )
    assert payload.sap_hana_encrypt is True
    assert payload.database_name is None


def test_schema_rejects_missing_database_name_for_other_engines():
    with pytest.raises(ValueError, match="database_name"):
        DirectConnectionCreate(db_type="postgresql", host="db.example.com", port=5432, username="auditor", password="x")


def test_schema_accepts_hana_without_a_database_name_but_others_still_need_one():
    hana_payload = DirectConnectionCreate(
        db_type="sap_hana", host="hana.example.com", port=39013, username="auditor", password="x", sap_hana_encrypt=False,
    )
    assert hana_payload.database_name is None
    postgres_payload = DirectConnectionCreate(
        db_type="postgresql", host="db.example.com", port=5432, database_name="auditdb", username="auditor", password="x",
    )
    assert postgres_payload.database_name == "auditdb"
