import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from app.core.canonical_model import CANONICAL_MODEL, infer_object_for_entity
from app.core.control_library_data import CONTROL_LIBRARY
from app.core.value_profile import (
    LOW_VALUE_FIT_THRESHOLD,
    ColumnInfo,
    build_column_profile,
    canonical_kind,
    column_kind_from_type,
    is_categorical_named,
    is_identifier_named,
    is_pii_named,
    score_table_fit,
    score_table_value_fit,
    score_value_fit,
)
from app.models.data_source import DataEntity, DataField, DataFieldProfile, DataSource
from app.services import data_source_service, mapping_service


# ---- what a canonical field expects -----------------------------------------
def test_canonical_kind_conventions():
    assert canonical_kind("user.status") == "enum"
    assert canonical_kind("user.last_login") == "datetime"
    assert canonical_kind("user.user_id") == "identifier"
    assert canonical_kind("payment.amount") == "numeric"
    assert canonical_kind("device.mfa_enabled") == "boolean"
    assert canonical_kind("role.description") is None


def test_most_canonical_fields_are_checkable():
    fields = [f for object_fields in CANONICAL_MODEL.values() for f in object_fields]
    checkable = sum(1 for f in fields if canonical_kind(f) is not None)
    assert checkable / len(fields) > 0.6


# ---- what a column is -------------------------------------------------------
@pytest.mark.parametrize(
    "data_type,expected",
    [
        ("VARCHAR(255)", "text"), ("TEXT", "text"), ("string", "text"), ("string (optional)", "text"),
        ("INTEGER", "numeric"), ("BIGINT", "numeric"), ("NUMERIC(10, 2)", "numeric"), ("double", "numeric"),
        ("TIMESTAMP", "datetime"), ("DATE", "datetime"), ("date", "datetime"), ("TIMESTAMP WITH TIME ZONE", "datetime"),
        ("BOOLEAN", "boolean"), ("boolean", "boolean"),
        ("UUID", "identifier"), ("objectid", "identifier"),
        ("INTERVAL", None), ("INET", None), ("array", None), ("mixed: boolean/string", None), (None, None),
    ],
)
def test_column_kind_from_type(data_type, expected):
    assert column_kind_from_type(data_type) == expected


# ---- type-level fit (works with no sampling at all, e.g. Gateway sources) ----
def test_datetime_field_on_a_matching_or_sloppy_or_wrong_type():
    assert score_value_fit("user.last_login", data_type="TIMESTAMP") == (100.0, None)
    good_enough, reason = score_value_fit("user.last_login", data_type="VARCHAR(50)")
    assert good_enough is not None and good_enough >= LOW_VALUE_FIT_THRESHOLD and reason
    contradiction, why = score_value_fit("user.last_login", data_type="BOOLEAN")
    assert contradiction is not None and contradiction < LOW_VALUE_FIT_THRESHOLD and why


def test_nothing_checkable_means_no_opinion_not_bad():
    assert score_value_fit("role.description", data_type="TEXT") == (None, None)
    assert score_value_fit("user.status") == (None, None)
    assert score_value_fit("user.status", data_type="JSONB") == (None, None)


# ---- profiles and the privacy policy ------------------------------------------
def test_empty_sample_has_no_profile():
    assert build_column_profile("status", []) is None


def test_enum_like_column_stores_its_values_lowercased():
    profile = build_column_profile("status", ["Active", "LOCKED", "active", None, "Active"])
    assert profile.top_values == ("active", "locked")
    assert profile.sample_size == 5 and profile.null_ratio == 0.2
    assert profile.value_kind == "text"


@pytest.mark.parametrize("name", ["password_hash", "email", "phone_number", "salary", "first_name", "iban", "api_token"])
def test_pii_named_columns_never_store_values(name):
    assert is_pii_named(name)
    assert build_column_profile(name, ["a", "b", "a"]).top_values is None


def test_pii_matching_is_not_over_eager():
    assert not is_pii_named("payment_status")
    assert not is_pii_named("shipping_method")
    assert not is_pii_named("status")


def test_sensitive_flag_blocks_values_even_for_a_harmless_name():
    assert build_column_profile("status", ["a", "b"], is_sensitive=True).top_values is None


def test_high_cardinality_long_and_identifier_like_columns_store_no_values():
    assert build_column_profile("code", [f"v{i}" for i in range(25)]).top_values is None
    assert build_column_profile("note", ["x" * 41, "y"]).top_values is None
    assert build_column_profile("owner", [str(uuid.uuid4()) for _ in range(3)]).top_values is None
    assert build_column_profile("seen", ["2024-01-01", "2024-02-01"]).top_values is None


def test_value_kind_is_the_clear_majority_or_unnamed():
    assert build_column_profile("x", [1, 2, 3, 4, 5]).value_kind == "numeric"
    assert build_column_profile("x", ["2024-01-01", "2024-01-02"]).value_kind == "datetime"
    assert build_column_profile("x", [datetime(2024, 1, 1)]).value_kind == "datetime"
    assert build_column_profile("x", [True, False]).value_kind == "boolean"
    assert build_column_profile("x", ["a", 1, "b", 2]).value_kind is None


# ---- value-level fit ----------------------------------------------------------
def _profile(field, values):
    return build_column_profile(field, values)


def test_status_full_of_genres_is_a_contradiction():
    genres = _profile("status", ["Comedy", "Drama", "Action", "Comedy", "Drama", "Horror"] * 3)
    score, reason = score_value_fit("user.status", data_type="TEXT", profile=genres)
    assert score is not None and score < LOW_VALUE_FIT_THRESHOLD
    assert "comedy" in reason and "status" in reason


def test_status_holding_account_states_fits():
    states = _profile("status", ["active", "locked", "disabled", "active", "active", "inactive"])
    assert score_value_fit("user.status", data_type="TEXT", profile=states) == (100.0, None)


def test_tiny_samples_are_not_judged_on_vocabulary():
    assert score_value_fit("user.status", data_type="TEXT", profile=_profile("status", ["premium", "basic"]))[0] == 100.0


def test_free_text_under_an_enum_field_is_a_contradiction():
    free_text = _profile("type", [f"free text number {i}" for i in range(30)])
    score, reason = score_value_fit("document.document_type", data_type="TEXT", profile=free_text)
    assert score is not None and score < LOW_VALUE_FIT_THRESHOLD and "too varied" in reason


def test_mostly_empty_key_is_a_contradiction():
    mostly_null = _profile("user_id", [None] * 9 + ["u1"])
    score, reason = score_value_fit("user.user_id", data_type="VARCHAR(20)", profile=mostly_null)
    assert score is not None and score < LOW_VALUE_FIT_THRESHOLD and "empty" in reason


def test_observed_values_beat_the_declared_type():
    iso_dates_in_text = _profile("last_login", ["2024-01-01", "2024-02-03", "2024-03-04"])
    assert score_value_fit("user.last_login", data_type="TEXT", profile=iso_dates_in_text) == (100.0, None)


# ---- table level ---------------------------------------------------------------
GOOD_USERS = [
    ColumnInfo("user_id", True, "UUID"), ColumnInfo("username", False, "TEXT"),
    ColumnInfo("status", False, "TEXT", _profile("status", ["active", "locked", "active", "disabled", "active"] * 3)),
    ColumnInfo("last_login", False, "TIMESTAMP"),
]
WRONG_KIND_USERS = [
    ColumnInfo("user_id", True, "BOOLEAN"), ColumnInfo("username", False, "TEXT"),
    ColumnInfo("status", False, "TEXT", _profile("status", ["Comedy", "Drama", "Action", "Horror", "Comedy"] * 4)),
    ColumnInfo("last_login", False, "BOOLEAN"),
]


def test_table_with_the_right_kinds_of_data_fits():
    fit = score_table_fit(GOOD_USERS, "users")
    assert fit.structural >= 50 and fit.value == 100.0 and fit.effective == fit.structural


def test_table_named_right_but_holding_the_wrong_kinds_is_flagged_by_value_alone():
    fit = score_table_fit(WRONG_KIND_USERS, "users")
    assert fit.structural >= 50  # the names look perfect...
    assert fit.value is not None and fit.value < LOW_VALUE_FIT_THRESHOLD  # ...the data does not
    assert fit.effective < LOW_VALUE_FIT_THRESHOLD


def test_one_checkable_column_is_not_enough_to_condemn_a_table():
    only_one = [ColumnInfo("user_id", True, "BOOLEAN"), ColumnInfo("username", False, "TEXT")]
    assert score_table_value_fit(only_one, "users") is None


def test_no_columns_or_no_modeled_object_is_not_applicable():
    assert score_table_fit([], "users") is None
    assert score_table_fit([ColumnInfo("a", False, "TEXT")], "service_agreements") is None


_GOOD_TYPE = {"identifier": "UUID", "datetime": "TIMESTAMP", "boolean": "BOOLEAN", "numeric": "NUMERIC(10, 2)", "enum": "TEXT"}
_WRONG_TYPE = {"identifier": "BOOLEAN", "datetime": "BOOLEAN", "boolean": "TIMESTAMP", "numeric": "UUID", "enum": "UUID"}


def _object_columns(obj, types):
    return [ColumnInfo(field, False, types.get(canonical_kind(field), "TEXT")) for field in CANONICAL_MODEL[obj]]


def test_value_check_holds_across_the_whole_control_library():
    """For every required table whose object has enough checkable fields: a
    table with the right kinds of data is never flagged, and one with the
    wrong kinds always is — whatever the object, so a future database is
    covered the same way `users` is."""
    tables = sorted({t for row in CONTROL_LIBRARY for t in row[4]})
    covered = 0
    for table in tables:
        obj = infer_object_for_entity(table)
        if obj is None or obj not in CANONICAL_MODEL:
            continue
        if sum(1 for f in CANONICAL_MODEL[obj] if canonical_kind(f)) < 2:
            continue
        covered += 1
        good = score_table_value_fit(_object_columns(obj, _GOOD_TYPE), table)
        bad = score_table_value_fit(_object_columns(obj, _WRONG_TYPE), table)
        assert good is not None and good >= LOW_VALUE_FIT_THRESHOLD, (table, good)
        assert bad is not None and bad < LOW_VALUE_FIT_THRESHOLD, (table, bad)
    assert covered >= 100


# ---- sampling ------------------------------------------------------------------
def test_sampler_reads_only_wanted_non_binary_columns_and_quotes_identifiers(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'client.db'}")
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE "weird table" (id INTEGER, "user status" TEXT, photo BLOB)'))
        conn.execute(text("INSERT INTO \"weird table\" VALUES (1, 'active', x'00'), (2, 'locked', x'01'), (3, 'active', x'02')"))
    monkeypatch.setattr(data_source_service, "_build_direct_engine", lambda connection: create_engine(f"sqlite:///{tmp_path / 'client.db'}"))

    rows = data_source_service._sample_sql_rows(
        SimpleNamespace(db_type="sqlite"),
        table_name="weird table",
        columns=[("id", "INTEGER"), ("user status", "TEXT"), ("photo", "BLOB")],
        limit=2,
    )
    assert len(rows) == 2
    assert set(rows[0]) == {"id", "user status"}  # the binary column was never selected


# ---- storing profiles and using them to demote a mapping ------------------------------
@pytest.fixture
def genre_status_entity(db, test_org):
    source = DataSource(organization_id=test_org.organization_id, source_name="profile test", source_type="postgresql", environment="cloud")
    db.add(source)
    db.flush()
    entity = DataEntity(data_source_id=source.data_source_id, entity_name="system_users")
    db.add(entity)
    db.flush()
    for name, data_type, pk in [("user_id", "TEXT", True), ("username", "TEXT", False), ("status", "TEXT", False), ("last_login", "TEXT", False), ("email", "TEXT", False)]:
        db.add(DataField(entity_id=entity.entity_id, field_name=name, data_type=data_type, is_primary_key=pk))
    db.commit()
    yield entity
    db.delete(source)
    db.commit()


def _fake_rows(status_values):
    return [
        {"user_id": f"u{i}", "username": f"name{i}", "status": status, "last_login": "2024-05-01", "email": f"x{i}@example.com"}
        for i, status in enumerate(status_values)
    ]


def test_profiling_stores_stats_never_pii_values_and_refreshes(db, genre_status_entity, monkeypatch):
    entity = genre_status_entity
    monkeypatch.setattr(data_source_service, "fetch_profile_rows", lambda *a, **k: _fake_rows(["Comedy", "Drama", "Comedy", "Action", "Drama", "Horror"] * 3))
    assert data_source_service.profile_entity_columns(db, entity_id=entity.entity_id, connections=[SimpleNamespace()]) == 5

    profiles = {
        f.field_name: p
        for f, p in db.query(DataField, DataFieldProfile).join(DataFieldProfile, DataFieldProfile.field_id == DataField.field_id).filter(DataField.entity_id == entity.entity_id)
    }
    assert profiles["status"].top_values == ["action", "comedy", "drama", "horror"]
    assert profiles["email"].top_values is None  # PII-named: statistics only
    assert profiles["user_id"].top_values is None and profiles["username"].top_values is None  # identifier / personal name: statistics only
    assert profiles["last_login"].value_kind == "datetime" and profiles["last_login"].top_values is None

    # An empty table must not leave a stale profile behind.
    monkeypatch.setattr(data_source_service, "fetch_profile_rows", lambda *a, **k: [])
    data_source_service.profile_entity_columns(db, entity_id=entity.entity_id, connections=[SimpleNamespace()])
    db.expire_all()
    assert db.query(DataFieldProfile).join(DataField, DataField.field_id == DataFieldProfile.field_id).filter(DataField.entity_id == entity.entity_id).count() == 0


def test_profiling_reports_none_when_no_connection_can_be_read(db, genre_status_entity):
    assert data_source_service.profile_entity_columns(db, entity_id=genre_status_entity.entity_id, connections=[]) is None


def test_mapping_suggestion_is_demoted_when_the_data_contradicts_the_name(db, genre_status_entity, monkeypatch):
    entity = genre_status_entity
    before = {s.field_name: s for s in mapping_service.suggest_mappings_for_entity(db, entity_id=entity.entity_id)}

    monkeypatch.setattr(data_source_service, "fetch_profile_rows", lambda *a, **k: _fake_rows(["Comedy", "Drama", "Comedy", "Action", "Drama", "Horror"] * 3))
    data_source_service.profile_entity_columns(db, entity_id=entity.entity_id, connections=[SimpleNamespace()])
    after = {s.field_name: s for s in mapping_service.suggest_mappings_for_entity(db, entity_id=entity.entity_id)}

    status = after["status"]
    assert status.suggested_canonical_field.endswith("status")
    assert status.value_fit_score is not None and status.value_fit_score < LOW_VALUE_FIT_THRESHOLD
    assert status.confidence_score < 90 and status.value_fit_reason
    # Nothing is ever raised, and columns with no objection are untouched.
    assert all(after[name].confidence_score <= before[name].confidence_score for name in after)
    assert after["username"].confidence_score == before["username"].confidence_score
    assert after["username"].value_fit_score is None


# ---- Gateway-reported profiles -------------------------------------------------
import importlib.util
from pathlib import Path

from app.core.value_profile import sanitize_reported_profile
from app.schemas.data_source import DiscoveredEntity, DiscoveredField, DiscoveredFieldProfile, DiscoveryPayload

_GATEWAY_PROFILING = Path(__file__).resolve().parents[2] / "gateway" / "gateway" / "profiling.py"


def _load_gateway_profiling():
    spec = importlib.util.spec_from_file_location("gateway_profiling_under_test", _GATEWAY_PROFILING)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_DRIFT_CASES = [
    ("status", ["Active", "LOCKED", "active", None, "Active"], False),
    ("status", ["Comedy", "Drama"] * 15, False),
    ("status", ["a", "b"], True),
    ("password_hash", ["x", "y", "x"], False),
    ("email", ["a@b.com", "c@d.com"], False),
    ("payment_status", ["paid", "due", "paid"], False),
    ("last_login", ["2024-01-01", "2024-02-03", None], False),
    ("user_id", [str(uuid.uuid4()) for _ in range(6)], False),
    ("amount", [1, 2.5, 3, None, 4], False),
    ("is_admin", [True, False, True], False),
    ("code", [f"v{i}" for i in range(25)], False),
    ("note", ["x" * 41, "y", ""], False),
    ("mixed", ["a", 1, "b", 2], False),
    ("empty", [None, None, None], False),
    ("username", ["u1", "u2", "u1", "u2"], False),
    ("approved_by", ["alice", "bob"] * 3, False),
    ("description", ["x", "y"] * 3, False),
    ("amount", [100, 200] * 3, False),
    ("risk_level", ["high", "low"] * 3, False),
    ("document_type", ["invoice", "credit"] * 3, False),
    ("user_id", ["U1", "U2", "U3", "U4"], False),
    ("invoice_no", ["A", "B", "A", "B", "A", "B"], False),
    ("status", ["a", "b", "c", "d", "e"], False),
    ("status", ["a", "b"] * 4, False),
    ("status", ["a", "b", "a", "b", "c", "c", "a", "b"], False),
]


def test_gateway_and_platform_profile_builders_never_drift():
    """The Gateway ships its own copy of the profile builder (it cannot import
    from the backend). Same inputs must give the same profile, or a change to
    the privacy policy on one side would silently miss the other."""
    gateway = _load_gateway_profiling()
    for name, values, sensitive in _DRIFT_CASES:
        theirs = gateway.build_column_profile(name, list(values), is_sensitive=sensitive)
        ours = build_column_profile(name, list(values), is_sensitive=sensitive)
        assert (
            theirs["sample_size"], theirs["null_ratio"], theirs["distinct_count"],
            theirs["distinct_ratio"], theirs["value_kind"], theirs["max_length"],
        ) == (
            ours.sample_size, ours.null_ratio, ours.distinct_count,
            ours.distinct_ratio, ours.value_kind, ours.max_length,
        ), name
        assert (tuple(theirs["top_values"]) if theirs["top_values"] is not None else None) == ours.top_values, name
        DiscoveredFieldProfile(**theirs)  # the Gateway's wire shape must parse as the platform's schema
    for name in ["password", "first_name", "iban", "payment_status", "shipping_method", "status", "api_token", "dob", "syntax", "username", "user_id", "invoice_no", "display_name", "handle", "approved_by", "risk_level", "description", "amount"]:
        assert gateway.is_pii_named(name) == is_pii_named(name), name
        assert gateway.is_identifier_named(name) == is_identifier_named(name), name
        assert gateway.is_categorical_named(name) == is_categorical_named(name), name
    assert gateway.MAX_STORED_DISTINCT_VALUES == 20 and gateway.MAX_STORED_VALUE_LENGTH == 40


def _reported(**overrides):
    base = {
        "sample_size": 10, "null_ratio": 0.1, "distinct_count": 3, "distinct_ratio": 0.33,
        "value_kind": "text", "top_values": ["Active", "locked"], "max_length": 6,
    }
    return {**base, **overrides}


def test_platform_screens_reported_values_against_its_own_rules():
    ok = sanitize_reported_profile("status", False, _reported())
    assert ok.top_values == ("active", "locked")
    # A Gateway claiming values for a PII-named column, or one the auditor flagged sensitive here, is overruled.
    assert sanitize_reported_profile("email", False, _reported()).top_values is None
    assert sanitize_reported_profile("status", True, _reported()).top_values is None
    # Too many, too long, or identifier/date-like values are dropped however they were reported.
    assert sanitize_reported_profile("status", False, _reported(top_values=[f"v{i}" for i in range(25)])).top_values is None
    assert sanitize_reported_profile("status", False, _reported(top_values=["x" * 41])).top_values is None
    assert sanitize_reported_profile("status", False, _reported(value_kind="identifier")).top_values is None


def test_platform_clamps_and_rejects_malformed_reports():
    clamped = sanitize_reported_profile(
        "status", False, _reported(null_ratio=7, distinct_ratio=-1, distinct_count=999, value_kind="bogus")
    )
    assert clamped.null_ratio == 1.0 and clamped.distinct_ratio == 0.0 and clamped.distinct_count == 10
    assert clamped.value_kind is None
    assert sanitize_reported_profile("status", False, {"sample_size": 0}) is None
    assert sanitize_reported_profile("status", False, _reported(sample_size="lots")) is None
    assert sanitize_reported_profile("status", False, _reported(sample_size=10**9)) is None


def _payload(entity_name, fields):
    return DiscoveryPayload(entities=[DiscoveredEntity(entity_name=entity_name, fields=fields)])


def _wire(**overrides):
    return DiscoveredFieldProfile(**_reported(**overrides))


def test_gateway_discovery_stores_screened_profiles_and_keeps_them_when_omitted(db, test_org):
    source = DataSource(
        organization_id=test_org.organization_id, source_name="gateway profile test", source_type="postgresql", environment="cloud"
    )
    db.add(source)
    db.commit()
    plain = [
        DiscoveredField(field_name="status", data_type="TEXT"),
        DiscoveredField(field_name="email", data_type="TEXT"),
        DiscoveredField(field_name="username", data_type="TEXT"),
    ]
    try:
        data_source_service.replace_discovery(
            db,
            data_source_id=source.data_source_id,
            payload=_payload(
                "system_users",
                [
                    DiscoveredField(field_name="status", data_type="TEXT", profile=_wire()),
                    DiscoveredField(field_name="email", data_type="TEXT", profile=_wire()),
                    DiscoveredField(field_name="username", data_type="TEXT"),
                ],
            ),
        )

        def stored():
            db.expire_all()
            rows = (
                db.query(DataField.field_name, DataFieldProfile)
                .join(DataFieldProfile, DataFieldProfile.field_id == DataField.field_id)
                .join(DataEntity, DataEntity.entity_id == DataField.entity_id)
                .filter(DataEntity.data_source_id == source.data_source_id)
            )
            return {name: p for name, p in rows}

        first = stored()
        assert set(first) == {"status", "email"}  # username sent no profile
        assert first["status"].top_values == ["active", "locked"]
        assert first["email"].top_values is None  # PII-named: values stripped on arrival

        # A later discovery from a Gateway that sends no profile (or an older build) must not wipe them.
        data_source_service.replace_discovery(db, data_source_id=source.data_source_id, payload=_payload("system_users", plain))
        assert set(stored()) == {"status", "email"}

        # And a refreshed profile replaces the old one.
        data_source_service.replace_discovery(
            db,
            data_source_id=source.data_source_id,
            payload=_payload("system_users", [DiscoveredField(field_name="status", data_type="TEXT", profile=_wire(top_values=["disabled"]))]),
        )
        assert stored()["status"].top_values == ["disabled"]
    finally:
        db.delete(db.get(DataSource, source.data_source_id))
        db.commit()


def test_columns_that_were_never_sampled_get_no_profile(db, genre_status_entity, monkeypatch):
    """A binary column is skipped by the sampler; profiling it as all-empty
    would be a false statement about the data."""
    entity = genre_status_entity
    rows = [{"user_id": "u1", "username": "a", "status": "active"}, {"user_id": "u2", "username": "b", "status": "locked"}]
    monkeypatch.setattr(data_source_service, "fetch_profile_rows", lambda *a, **k: rows)
    assert data_source_service.profile_entity_columns(db, entity_id=entity.entity_id, connections=[SimpleNamespace()]) == 3
    names = {
        f.field_name
        for f in db.query(DataField)
        .join(DataFieldProfile, DataFieldProfile.field_id == DataField.field_id)
        .filter(DataField.entity_id == entity.entity_id)
    }
    assert names == {"user_id", "username", "status"}  # last_login and email were absent from every row


def test_a_column_with_no_stored_values_is_sql_null_not_the_json_value_null(db, genre_status_entity, monkeypatch):
    """`top_values IS NULL` must mean "no values stored": SQLAlchemy's JSONB otherwise writes the JSON
    value null for Python None, which IS NULL does not match (found in a live-data inspection: 155 rows)."""
    from sqlalchemy import text

    entity = genre_status_entity
    rows = [{"user_id": f"u{i}", "username": f"n{i}", "status": ["active", "locked"][i % 2], "last_login": "2024-05-01", "email": f"x{i}@example.com"} for i in range(6)]
    monkeypatch.setattr(data_source_service, "fetch_profile_rows", lambda *a, **k: rows)
    data_source_service.profile_entity_columns(db, entity_id=entity.entity_id, connections=[SimpleNamespace()])
    kinds = dict(
        db.execute(
            text(
                "select f.field_name, jsonb_typeof(p.top_values) from data_field_profiles p "
                "join data_fields f on f.field_id = p.field_id where f.entity_id = :e"
            ),
            {"e": entity.entity_id},
        ).all()
    )
    assert kinds["status"] == "array"
    assert kinds["email"] is None and kinds["last_login"] is None  # SQL NULL
    assert "null" not in kinds.values()


# ---- privacy: identifiers and names are not enums, however small the table --------------------------
def test_identifier_and_personal_name_columns_never_store_values_even_in_a_tiny_table():
    """Found by an end-to-end inspection: an 8-row users table has 8 user ids and 8 usernames, which
    a cardinality cap alone lets through. They must stay statistics-only."""
    for name in ("user_id", "username", "invoice_no", "api_key", "employee_ref", "display_name", "handle"):
        profile = build_column_profile(name, ["a1", "b2", "a1", "b2", "a1", "b2"])
        assert profile.top_values is None, name


def test_only_repetitive_columns_are_enum_like():
    assert build_column_profile("status", ["a", "b", "c", "d", "e"]).top_values is None  # one value per row: a key, not an enum
    assert build_column_profile("status", ["a", "b"] * 4).top_values == ("a", "b")
    assert build_column_profile("status", ["active", "locked", "disabled"] * 5).top_values == ("active", "disabled", "locked")


def test_a_reported_profile_with_identifier_or_username_values_is_stripped_on_arrival():
    """An older or modified Gateway may send values it should not: the platform re-screens them."""
    sent = _reported(top_values=["u001", "u002"], distinct_count=2, sample_size=20)
    assert sanitize_reported_profile("user_id", False, sent).top_values is None
    assert sanitize_reported_profile("username", False, sent).top_values is None
    assert sanitize_reported_profile("status", False, sent).top_values == ("u001", "u002")  # a plausible enum survives
    all_distinct = _reported(top_values=[f"v{i}" for i in range(9)], distinct_count=9, sample_size=10, null_ratio=0.1)
    assert sanitize_reported_profile("status", False, all_distinct).top_values is None  # ~one value per row


def test_only_columns_named_like_a_category_may_keep_values():
    """Data minimisation: values are only ever used to check that a status/state column holds states,
    so people (approved_by), free text (description), money (amount) and names stay statistics-only
    however much they repeat. Found by an inspection of what was actually stored."""
    repeated = ["alice", "bob"] * 4
    for name in ("approved_by", "created_by", "description", "asset_name", "country", "department", "dataset"):
        assert build_column_profile(name, repeated).top_values is None, name
    assert build_column_profile("amount", [100, 200] * 4).top_values is None  # numbers are never kept
    for name in ("status", "account_status", "risk_level", "document_type", "severity", "employment_status"):
        assert build_column_profile(name, ["a", "b"] * 4).top_values == ("a", "b"), name
    assert is_categorical_named("Account Status") and not is_categorical_named("statuses_id")
