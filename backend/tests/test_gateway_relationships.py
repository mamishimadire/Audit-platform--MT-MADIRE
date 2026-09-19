"""
The Gateway relationship channel, end to end: a Gateway authenticates with its real
device key, asks which column pairs to measure, measures them on ITS OWN database with
the Gateway's own measurement code, and posts back counts only.
"""
import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.security import fingerprint, generate_gateway_api_key
from app.main import app
from app.models.data_source import DataConnection, DataEntity, DataField, DataRelationship, DataSource, Gateway
from app.schemas.data_source import DiscoveredEntity, DiscoveredField, DiscoveryPayload
from app.services import data_source_service, gateway_relationship_service

client = TestClient(app)

_GATEWAY_RELATIONSHIPS = Path(__file__).resolve().parents[2] / "gateway" / "gateway" / "relationships.py"


def _gateway_module():
    spec = importlib.util.spec_from_file_location("gateway_relationships_under_test", _GATEWAY_RELATIONSHIPS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client_db(tmp_path):
    """The CLIENT's database, which only the Gateway can reach."""
    path = tmp_path / "client.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (user_id TEXT, username TEXT)"))
        conn.execute(text("CREATE TABLE api_access (api_id TEXT, user_id TEXT)"))
        conn.execute(text("CREATE TABLE roles (role_id TEXT, role_name TEXT)"))
        conn.execute(text("CREATE TABLE user_roles (user_id TEXT, role TEXT)"))
        for i in range(1, 9):
            conn.execute(text("INSERT INTO users VALUES (:u, :n)"), {"u": f"U{i}", "n": f"n{i}"})
        for i in range(1, 7):
            conn.execute(text("INSERT INTO api_access VALUES ('API1', :u)"), {"u": f"U{i}"})
            conn.execute(text("INSERT INTO user_roles VALUES (:u, :r)"), {"u": f"U{i}", "r": f"role_{i}"})
            conn.execute(text("INSERT INTO roles VALUES (:i, :n)"), {"i": f"R{i:02d}", "n": f"role_{i}"})
    engine.dispose()
    return path


@pytest.fixture
def gateway_source(db, test_org):
    """A Gateway-only data source: a registered Gateway, one gateway connection, discovered tables."""
    api_key = generate_gateway_api_key()
    gateway = Gateway(
        organization_id=test_org.organization_id, gateway_name="rel test gw", registration_status="registered",
        status="online", device_certificate_fingerprint=fingerprint(api_key),
    )
    source = DataSource(organization_id=test_org.organization_id, source_name="gw relationship test", source_type="postgresql", environment="on_premise")
    db.add_all([gateway, source])
    db.flush()
    connection = DataConnection(data_source_id=source.data_source_id, gateway_id=gateway.gateway_id, connection_mode="gateway", connection_status="connected")
    db.add(connection)
    db.commit()
    data_source_service.replace_discovery(
        db,
        data_source_id=source.data_source_id,
        payload=DiscoveryPayload(
            entities=[
                DiscoveredEntity(entity_name="users", fields=[DiscoveredField(field_name="user_id", data_type="TEXT", is_unique=True), DiscoveredField(field_name="username", data_type="TEXT")]),
                DiscoveredEntity(entity_name="api_access", fields=[DiscoveredField(field_name="api_id", data_type="TEXT"), DiscoveredField(field_name="user_id", data_type="TEXT")]),
                DiscoveredEntity(entity_name="roles", fields=[DiscoveredField(field_name="role_id", data_type="TEXT", is_unique=True), DiscoveredField(field_name="role_name", data_type="TEXT", is_unique=True)]),
                DiscoveredEntity(entity_name="user_roles", fields=[DiscoveredField(field_name="user_id", data_type="TEXT"), DiscoveredField(field_name="role", data_type="TEXT")]),
            ]
        ),
    )
    headers = {"X-Gateway-Id": str(gateway.gateway_id), "X-Api-Key": api_key}
    yield SimpleNamespace(gateway=gateway, source=source, connection=connection, headers=headers)
    db.delete(db.get(DataSource, source.data_source_id))
    db.delete(db.get(Gateway, gateway.gateway_id))
    db.commit()


def _edges(db, source):
    db.expire_all()
    names = {}
    for f in db.query(DataField).join(DataEntity, DataEntity.entity_id == DataField.entity_id).filter(DataEntity.data_source_id == source.data_source_id):
        names[f.field_id] = f"{db.get(DataEntity, f.entity_id).entity_name}.{f.field_name}"
    return {
        (names[e.child_field_id], names[e.parent_field_id]): e
        for e in db.query(DataRelationship).filter(DataRelationship.data_source_id == source.data_source_id, DataRelationship.kind == "inferred")
    }


def _measure_like_the_gateway(client_db, pairs):
    """What the Gateway does: measure each requested pair on ITS database, counts only."""
    gateway = _gateway_module()
    engine = create_engine(f"sqlite:///{client_db}")
    try:
        def one(pair):
            with engine.connect() as conn:
                return gateway.measure_sql(conn, pair)

        return gateway.measure_all(one, pairs)
    finally:
        engine.dispose()


def test_a_gateway_measures_on_its_own_database_and_only_counts_come_back(db, gateway_source, client_db):
    gw = gateway_source
    # 1. It asks what to measure.
    resp = client.get(f"/api/v1/gateways/{gw.gateway.gateway_id}/relationship-requests", headers=gw.headers)
    assert resp.status_code == 200, resp.text
    requests_ = resp.json()
    assert [r["connection_id"] for r in requests_] == [str(gw.connection.connection_id)]
    pairs = requests_[0]["pairs"]
    by_name = {(p["child_entity"], p["child_column"], p["parent_entity"], p["parent_column"]): p for p in pairs}
    assert ("api_access", "user_id", "users", "user_id") in by_name
    assert ("user_roles", "role", "roles", "role_name") in by_name

    # 2. It measures locally and reports counts.
    measurements = _measure_like_the_gateway(client_db, pairs)
    allowed = {"child_field_id", "parent_field_id", "child_distinct", "matched_distinct", "parent_distinct", "parent_rows", "capped"}
    assert measurements and all(set(m) == allowed for m in measurements)  # counts and ids: never a value
    posted = client.post(
        f"/api/v1/gateways/{gw.gateway.gateway_id}/connections/{gw.connection.connection_id}/relationship-measurements",
        json={"measurements": measurements},
        headers=gw.headers,
    )
    assert posted.status_code == 200 and posted.json()["stored"] >= 2

    # 3. The platform now knows how the client's tables relate, without ever seeing a value.
    edges = _edges(db, gw.source)
    assert edges[("api_access.user_id", "users.user_id")].containment == 100.0
    assert edges[("user_roles.role", "roles.role_name")].containment == 100.0
    assert ("user_roles.role", "roles.role_id") not in edges  # role names never match R01-style ids

    # 4. It is not asked again until the measurement goes stale.
    again = client.get(f"/api/v1/gateways/{gw.gateway.gateway_id}/relationship-requests", headers=gw.headers)
    assert again.json() == []
    db.expire_all()
    assert db.get(DataConnection, gw.connection.connection_id).relationships_measured_at is not None


def test_a_gateway_can_never_introduce_an_edge_the_platform_did_not_ask_about(db, gateway_source):
    gw = gateway_source
    fields = {(db.get(DataEntity, f.entity_id).entity_name, f.field_name): f.field_id for f in db.query(DataField).join(DataEntity, DataEntity.entity_id == DataField.entity_id).filter(DataEntity.data_source_id == gw.source.data_source_id)}
    forged = {"child_field_id": str(fields[("users", "username")]), "parent_field_id": str(fields[("roles", "role_id")]), "child_distinct": 8, "matched_distinct": 8, "parent_distinct": 6, "parent_rows": 6}
    resp = client.post(
        f"/api/v1/gateways/{gw.gateway.gateway_id}/connections/{gw.connection.connection_id}/relationship-measurements",
        json={"measurements": [forged, {**forged, "child_field_id": str(uuid.uuid4())}]},
        headers=gw.headers,
    )
    assert resp.status_code == 200 and resp.json()["stored"] == 0
    assert _edges(db, gw.source) == {}


def test_impossible_numbers_are_ignored(db, gateway_source):
    gw = gateway_source
    request = client.get(f"/api/v1/gateways/{gw.gateway.gateway_id}/relationship-requests", headers=gw.headers).json()[0]["pairs"][0]
    ids = {"child_field_id": request["child_field_id"], "parent_field_id": request["parent_field_id"]}
    for bad in (
        {"child_distinct": 5, "matched_distinct": 9, "parent_distinct": 5, "parent_rows": 5},  # more matched than exist
        {"child_distinct": -1, "matched_distinct": 0, "parent_distinct": 5, "parent_rows": 5},
        {"child_distinct": 5, "matched_distinct": 5, "parent_distinct": 9, "parent_rows": 5},  # more distinct than rows
    ):
        resp = client.post(
            f"/api/v1/gateways/{gw.gateway.gateway_id}/connections/{gw.connection.connection_id}/relationship-measurements",
            json={"measurements": [{**ids, **bad}]},
            headers=gw.headers,
        )
        assert resp.json()["stored"] == 0
    assert _edges(db, gw.source) == {}


def test_a_partial_report_never_erases_edges_from_an_earlier_measurement(db, gateway_source, client_db):
    gw = gateway_source
    pairs = client.get(f"/api/v1/gateways/{gw.gateway.gateway_id}/relationship-requests", headers=gw.headers).json()[0]["pairs"]
    url = f"/api/v1/gateways/{gw.gateway.gateway_id}/connections/{gw.connection.connection_id}/relationship-measurements"
    full = _measure_like_the_gateway(client_db, pairs)
    assert client.post(url, json={"measurements": full}, headers=gw.headers).json()["stored"] >= 2
    before = set(_edges(db, gw.source))

    only_one = [m for m in full if m["child_field_id"] == next(p["child_field_id"] for p in pairs if (p["child_entity"], p["child_column"]) == ("api_access", "user_id"))]
    assert only_one
    client.post(url, json={"measurements": only_one}, headers=gw.headers)
    assert set(_edges(db, gw.source)) == before  # the pairs it did not report on are untouched


def test_the_route_refuses_another_gateways_id_and_bad_credentials(db, gateway_source):
    gw = gateway_source
    other = uuid.uuid4()
    assert client.get(f"/api/v1/gateways/{other}/relationship-requests", headers=gw.headers).status_code == 403
    assert client.get(f"/api/v1/gateways/{gw.gateway.gateway_id}/relationship-requests", headers={**gw.headers, "X-Api-Key": "wrong"}).status_code == 401
    assert (
        client.post(
            f"/api/v1/gateways/{gw.gateway.gateway_id}/connections/{uuid.uuid4()}/relationship-measurements",
            json={"measurements": []}, headers=gw.headers,
        ).status_code
        == 404
    )


def test_a_source_with_a_connected_direct_connection_is_measured_by_the_platform_not_the_gateway(db, gateway_source, monkeypatch):
    monkeypatch.setattr(gateway_relationship_service, "_connected_direct_connections", lambda db_, sid: [SimpleNamespace()])
    resp = client.get(f"/api/v1/gateways/{gateway_source.gateway.gateway_id}/relationship-requests", headers=gateway_source.headers)
    assert resp.json() == []


def test_gateway_and_platform_measurement_never_drift(client_db):
    """The Gateway ships its own copy of the measurement (it cannot import from the backend).
    On the same database, same pairs, both must produce identical counts."""
    gateway = _gateway_module()
    engine = create_engine(f"sqlite:///{client_db}")
    from app.core.relationship_inference import Candidate, ColumnRef

    def ref(entity, name, dtype="TEXT"):
        return ColumnRef(field_id=f"{entity}.{name}", entity_id=entity, entity_name=entity, name=name, data_type=dtype)

    cases = [
        ("api_access", "user_id", "TEXT", "users", "user_id", "TEXT"),
        ("users", "user_id", "TEXT", "api_access", "user_id", "TEXT"),
        ("user_roles", "role", "TEXT", "roles", "role_name", "TEXT"),
        ("user_roles", "role", "TEXT", "roles", "role_id", "TEXT"),
        ("api_access", "user_id", "TEXT", "roles", "role_id", "VARCHAR(20)"),  # differing types: compared as text on both sides
    ]
    try:
        for child_entity, child_col, child_type, parent_entity, parent_col, parent_type in cases:
            candidate = Candidate(ref(child_entity, child_col, child_type), ref(parent_entity, parent_col, parent_type), 1.0, False)
            with engine.connect() as conn:
                platform = data_source_service._measure_sql(conn, candidate)
            pair = {
                "child_field_id": "c", "parent_field_id": "p", "child_entity": child_entity, "child_column": child_col, "child_type": child_type,
                "parent_entity": parent_entity, "parent_column": parent_col, "parent_type": parent_type,
            }
            with engine.connect() as conn:
                theirs = gateway.measure_sql(conn, pair)
            assert (theirs["child_distinct"], theirs["matched_distinct"], theirs["parent_distinct"], theirs["parent_rows"]) == (
                platform.child_distinct, platform.matched_distinct, platform.parent_distinct, platform.parent_rows,
            ), (child_entity, child_col, parent_entity, parent_col)
    finally:
        engine.dispose()

