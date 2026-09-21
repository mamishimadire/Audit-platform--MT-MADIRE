"""
The file-upload connection end to end: over real HTTP with real users and roles, against the real
database (which is the same one production uses, so every test cleans up after itself and the last
test proves it by query).

What must hold: a file becomes a discoverable, mappable, testable table; a re-upload replaces the data
without losing the table's identity (its mappings), and warns instead of silently breaking a mapping;
nobody outside the organization, and nobody without data_sources:manage, can add or remove files; a
hostile or broken file is refused cleanly; several connections may share one data source without
deleting each other's tables.
"""
import hashlib
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.api.v1.routes import data_sources as routes
from app.core.security import create_access_token
from app.main import app
from app.models.audit_test import AuditTest, TestDataMapping, TestRule
from app.models.data_source import DataConnection, DataEntity, DataField, DataFile, DataRelationship
from app.models.monitoring import MonitoringSchedule
from app.services import data_source_service, direct_execution_service
from app.services.connectors import EntityNotHere, file_connector

# A real client address: audit_logs.ip_address is an INET column and rejects Starlette's default host.
client = TestClient(app, client=("127.0.0.1", 50000))


def _auth(user):
    return {"Authorization": f"Bearer {create_access_token(user_id=user.user_id, organization_id=user.organization_id)}"}


@pytest.fixture
def world(connector_world):
    return connector_world


def _connect(world, name="Payroll files") -> str:
    response = client.post(
        f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector",
        json={"db_type": "file_upload", "connection_name": name}, headers=_auth(world.manager),
    )
    assert response.status_code == 201, response.text
    return response.json()["connection_id"]


def _upload(connection_id, name, content, user, content_type="text/csv"):
    data = content.encode() if isinstance(content, str) else content
    return client.post(f"/api/v1/connections/{connection_id}/files", files={"file": (name, data, content_type)}, headers=_auth(user))


PAYROLL_V1 = "emp_id,name,salary\nE1,Ann,1000.5\nE2,Bob,900\nE3,Cy,2500\n"


def test_upload_discover_and_read_a_csv_end_to_end(db, world):
    connection_id = _connect(world)
    body = PAYROLL_V1.encode()
    response = _upload(connection_id, "payroll.csv", body, world.manager)
    assert response.status_code == 201, response.text
    uploaded = response.json()
    assert uploaded["sha256"] == hashlib.sha256(body).hexdigest() and uploaded["size_bytes"] == len(body)
    assert uploaded["is_current"] is True and uploaded["discovered"] is True and uploaded["warnings"] == []
    assert world.profiling_requests == [world.source.data_source_id]  # column reading was requested, in the background

    listed = client.get(f"/api/v1/data-sources/{world.source.data_source_id}/connections", headers=_auth(world.manager)).json()
    assert [c["connection_status"] for c in listed] == ["connected"] and listed[0]["connector_config"] is None
    assert "encrypted_password" not in listed[0] and "file_data" not in json.dumps(listed)

    entities = data_source_service.list_entities(db, data_source_id=world.source.data_source_id)
    assert [(e.entity_name, e.entity_type) for e in entities] == [("payroll", "file")]
    fields = {f.field_name: f.data_type for f in data_source_service.list_fields(db, entity_id=entities[0].entity_id)}
    assert fields == {"emp_id": "string", "name": "string", "salary": "double"}

    connection = db.get(DataConnection, uuid.UUID(connection_id))
    rows = data_source_service.fetch_direct_records(connection, entity_name="payroll", field_names=["emp_id", "salary"], limit=10)
    assert rows == [{"emp_id": "E1", "salary": 1000.5}, {"emp_id": "E2", "salary": 900.0}, {"emp_id": "E3", "salary": 2500.0}]
    assert len(data_source_service.fetch_direct_records(connection, entity_name="payroll", field_names=["emp_id"], limit=2)) == 2


def test_a_reupload_replaces_the_data_but_keeps_the_table_and_warns_before_breaking_a_mapping(db, world):
    connection_id = _connect(world)
    assert _upload(connection_id, "payroll.csv", PAYROLL_V1, world.manager).status_code == 201
    entity_id = data_source_service.list_entities(db, data_source_id=world.source.data_source_id)[0].entity_id
    connection = db.get(DataConnection, uuid.UUID(connection_id))

    # Same columns, new data: the table keeps its identity (so mappings survive) and the data changes.
    second = _upload(connection_id, "payroll.csv", "emp_id,name,salary\nE1,Ann,1.5\nE9,Zed,7\n", world.manager)
    assert second.status_code == 201 and second.json()["warnings"] == [] and second.json()["discovered"] is True
    db.expire_all()
    assert data_source_service.list_entities(db, data_source_id=world.source.data_source_id)[0].entity_id == entity_id
    assert data_source_service.fetch_direct_records(connection, entity_name="payroll", field_names=["emp_id"], limit=10) == [{"emp_id": "E1"}, {"emp_id": "E9"}]

    versions = client.get(f"/api/v1/connections/{connection_id}/files", headers=_auth(world.read_only)).json()  # any org member may look
    assert len(versions) == 2 and [v["is_current"] for v in versions].count(True) == 1
    assert next(v for v in versions if v["is_current"])["sha256"] == second.json()["sha256"]
    assert "file_data" not in json.dumps(versions)

    # A version that drops a column would delete the mappings pointing at it, so it is stored but the
    # catalogue is left alone until the user runs Discover schema on purpose.
    third = _upload(connection_id, "payroll.csv", "emp_id,name\nE1,Ann\n", world.manager)
    assert third.status_code == 201
    assert third.json()["discovered"] is False and any("salary" in w for w in third.json()["warnings"])
    db.expire_all()
    names = {f.field_name for f in data_source_service.list_fields(db, entity_id=entity_id)}
    assert "salary" in names


def test_a_hostile_or_broken_upload_is_refused_and_nothing_is_stored(db, world, monkeypatch):
    connection_id = _connect(world)
    assert _upload(connection_id, "malware.exe", "MZ", world.manager).status_code == 400
    assert "Only" in _upload(connection_id, "notes.pdf", "x", world.manager).json()["detail"]
    assert _upload(connection_id, "empty.csv", "", world.manager).status_code == 400
    assert _upload(connection_id, "bad.xlsx", "this is not a workbook", world.manager).status_code == 400
    monkeypatch.setattr(routes, "MAX_FILE_BYTES", 50)
    too_big = _upload(connection_id, "big.csv", "a\n" + "1\n" * 200, world.manager)
    assert too_big.status_code == 413
    assert client.get(f"/api/v1/connections/{connection_id}/files", headers=_auth(world.manager)).json() == []
    assert db.query(DataFile).filter(DataFile.connection_id == uuid.UUID(connection_id)).count() == 0


def test_a_path_in_the_file_name_is_reduced_to_the_name(db, world):
    connection_id = _connect(world)
    response = _upload(connection_id, "..\\..\\etc/passwd.csv", "a\n1\n", world.manager)
    assert response.status_code == 201 and response.json()["file_name"] == "passwd.csv"


def test_only_managers_of_the_same_organization_can_change_files(db, world):
    connection_id = _connect(world)
    stored = _upload(connection_id, "payroll.csv", PAYROLL_V1, world.manager).json()
    file_id = stored["file_id"]

    # No data_sources:manage permission: may look, may not change.
    assert client.get(f"/api/v1/connections/{connection_id}/files", headers=_auth(world.read_only)).status_code == 200
    assert _upload(connection_id, "x.csv", "a\n1\n", world.read_only).status_code == 403
    assert client.delete(f"/api/v1/connections/{connection_id}/files/{file_id}", headers=_auth(world.read_only)).status_code == 403
    assert client.post(
        f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector", json={"db_type": "file_upload"}, headers=_auth(world.read_only)
    ).status_code == 403

    # Another tenant's administrator cannot see, add to, or remove from this tenant's files.
    assert client.get(f"/api/v1/connections/{connection_id}/files", headers=_auth(world.outsider)).status_code == 403
    assert _upload(connection_id, "x.csv", "a\n1\n", world.outsider).status_code == 403
    assert client.delete(f"/api/v1/connections/{connection_id}/files/{file_id}", headers=_auth(world.outsider)).status_code == 403
    assert client.post(
        f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector", json={"db_type": "file_upload"}, headers=_auth(world.outsider)
    ).status_code == 403

    # No token at all.
    assert client.get(f"/api/v1/connections/{connection_id}/files").status_code in (401, 403)
    assert db.query(DataFile).filter(DataFile.connection_id == uuid.UUID(connection_id)).count() == 1  # nothing above changed anything


def test_a_file_id_only_works_on_the_connection_it_belongs_to(db, world):
    first, second = _connect(world, "one"), _connect(world, "two")
    stored = _upload(first, "payroll.csv", PAYROLL_V1, world.manager).json()
    assert client.delete(f"/api/v1/connections/{second}/files/{stored['file_id']}", headers=_auth(world.manager)).status_code == 404
    assert db.get(DataFile, uuid.UUID(stored["file_id"])) is not None


def test_uploads_are_only_accepted_by_a_file_connection(db, world):
    gateway_style = DataConnection(data_source_id=world.source.data_source_id, connection_mode="direct", db_type="postgresql", host="example.com", connection_status="connected")
    db.add(gateway_style)
    db.commit()
    response = _upload(str(gateway_style.connection_id), "x.csv", "a\n1\n", world.manager)
    assert response.status_code == 400 and "file connection" in response.json()["detail"]


def test_a_file_connection_takes_no_settings_and_unbuilt_families_are_refused(world):
    url = f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector"
    assert client.post(url, json={"db_type": "file_upload", "config": {"remote_path": "/x"}}, headers=_auth(world.manager)).status_code == 400
    assert client.post(url, json={"db_type": "file_upload", "secrets": {"password": "x"}}, headers=_auth(world.manager)).status_code == 400
    assert client.post(url, json={"db_type": "carrier_pigeon"}, headers=_auth(world.manager)).status_code == 422


def test_deleting_the_current_version_promotes_the_previous_one_and_the_last_one_disconnects(db, world):
    connection_id = _connect(world)
    v1 = _upload(connection_id, "payroll.csv", PAYROLL_V1, world.manager).json()
    v2 = _upload(connection_id, "payroll.csv", "emp_id,name,salary\nE7,New,5\n", world.manager).json()
    connection = db.get(DataConnection, uuid.UUID(connection_id))

    assert client.delete(f"/api/v1/connections/{connection_id}/files/{v2['file_id']}", headers=_auth(world.manager)).status_code == 204
    db.expire_all()
    assert db.get(DataFile, uuid.UUID(v1["file_id"])).is_current is True
    assert len(data_source_service.fetch_direct_records(connection, entity_name="payroll", field_names=["emp_id"], limit=10)) == 3

    assert client.delete(f"/api/v1/connections/{connection_id}/files/{v1['file_id']}", headers=_auth(world.manager)).status_code == 204
    db.expire_all()
    assert db.get(DataConnection, uuid.UUID(connection_id)).connection_status == "failed"  # nothing left to read
    with pytest.raises(EntityNotHere):
        data_source_service.fetch_direct_records(connection, entity_name="payroll", field_names=["emp_id"], limit=10)


def test_only_a_bounded_number_of_versions_is_kept(db, world, monkeypatch):
    monkeypatch.setattr(file_connector, "MAX_VERSIONS_PER_FILE", 2)
    connection_id = _connect(world)
    for i in range(4):
        assert _upload(connection_id, "p.csv", f"a\n{i}\n", world.manager).status_code == 201
    versions = client.get(f"/api/v1/connections/{connection_id}/files", headers=_auth(world.manager)).json()
    assert len(versions) == 2 and versions[0]["is_current"] is True


def test_a_workbook_with_two_sheets_is_two_tables(db, world):
    import io

    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "Staff"
    wb["Staff"].append(["emp_id", "grade"])
    wb["Staff"].append(["E1", 3])
    wb.create_sheet("Leavers").append(["emp_id", "left_on"])
    buf = io.BytesIO()
    wb.save(buf)
    connection_id = _connect(world)
    response = _upload(connection_id, "hr.xlsx", buf.getvalue(), world.manager, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert response.status_code == 201, response.text
    names = {e.entity_name for e in data_source_service.list_entities(db, data_source_id=world.source.data_source_id)}
    assert names == {"hr / Staff", "hr / Leavers"}


def test_two_connections_share_a_source_without_deleting_each_others_tables(db, world):
    a, b = _connect(world, "employees"), _connect(world, "payroll")
    assert _upload(a, "employees.csv", "emp_id,name\nE1,Ann\nE2,Bob\n", world.manager).json()["discovered"] is True
    assert _upload(b, "payroll.csv", PAYROLL_V1, world.manager).json()["discovered"] is True
    names = {e.entity_name for e in data_source_service.list_entities(db, data_source_id=world.source.data_source_id)}
    assert names == {"employees", "payroll"}  # discovering the second connection did not remove the first one's table

    # Each table is read from the connection that has it.
    connections = data_source_service._connected_direct_connections(db, world.source.data_source_id)
    assert len(connections) == 2
    got = {}
    for name in ("employees", "payroll"):
        got[name] = direct_execution_service._fetch_from_any(connections, entity_name=name, field_names=["emp_id"])
    assert [r["emp_id"] for r in got["employees"]] == ["E1", "E2"] and len(got["payroll"]) == 3


def test_relationships_between_uploaded_files_are_found_on_their_data(db, world):
    a, b = _connect(world, "employees"), _connect(world, "payroll")
    employees = "emp_id,name\n" + "".join(f"E{i:03d},Person {i}\n" for i in range(30))
    payroll = "pay_id,emp_id,amount\n" + "".join(f"P{i:03d},E{i % 30:03d},{100 + i}\n" for i in range(60))
    assert _upload(a, "employees.csv", employees, world.manager).status_code == 201
    assert _upload(b, "payroll.csv", payroll, world.manager).status_code == 201

    connections = data_source_service._connected_direct_connections(db, world.source.data_source_id)
    for entity in data_source_service.list_entities(db, data_source_id=world.source.data_source_id):
        assert data_source_service.profile_entity_columns(db, entity_id=entity.entity_id, connections=connections) is not None
    found = data_source_service.infer_relationships_for_source(db, data_source_id=world.source.data_source_id)
    assert found and found >= 1

    db.expire_all()
    edges = db.query(DataRelationship).filter(DataRelationship.data_source_id == world.source.data_source_id, DataRelationship.kind == "inferred").all()
    fields = {f.field_id: (db.get(DataEntity, f.entity_id).entity_name, f.field_name) for f in db.query(DataField).join(DataEntity, DataEntity.entity_id == DataField.entity_id).filter(DataEntity.data_source_id == world.source.data_source_id)}
    described = {(fields[e.child_field_id], fields[e.parent_field_id]) for e in edges}
    assert (("payroll", "emp_id"), ("employees", "emp_id")) in described
    edge = next(e for e in edges if fields[e.child_field_id] == ("payroll", "emp_id"))
    assert float(edge.containment) == 100.0


def test_a_control_rule_runs_on_an_uploaded_file(db, world):
    """Upload -> discover -> map -> the platform's own test runner executes the rule on the file's rows."""
    connection_id = _connect(world)
    assert _upload(connection_id, "payroll.csv", PAYROLL_V1, world.manager).status_code == 201
    entity = data_source_service.list_entities(db, data_source_id=world.source.data_source_id)[0]
    salary = next(f for f in data_source_service.list_fields(db, entity_id=entity.entity_id) if f.field_name == "salary")

    test = AuditTest(organization_id=world.org.organization_id, test_name="salary over 1000 (file)")
    db.add(test)
    db.flush()
    db.add(TestRule(
        audit_test_id=test.audit_test_id, rule_name="salary > 1000", rule_type="threshold", status="active",
        rule_definition=json.dumps({"rule_type": "threshold", "object": "payroll", "field": "salary", "operator": "gt", "value": 1000}),
    ))
    db.add(TestDataMapping(
        audit_test_id=test.audit_test_id, data_source_id=world.source.data_source_id, entity_id=entity.entity_id,
        field_id=salary.field_id, canonical_field="payroll.salary", mapping_status="approved",
    ))
    db.add(MonitoringSchedule(audit_test_id=test.audit_test_id, frequency="daily", status="active", is_active=True))
    db.commit()
    try:
        # Only THIS test is run: the shared database holds real organizations' schedules, and a test must
        # never execute (or record a run for) anything that is not its own.
        mine = [d for d in direct_execution_service._resolve_due_direct_tests(db) if d.audit_test_id == test.audit_test_id]
        assert len(mine) == 1
        report = direct_execution_service._run_one(mine[0])
        assert report.error_message is None
        assert report.records_analyzed == 3 and len(report.exceptions) == 2  # 1000.5 and 2500 are over 1000; 900 is not
    finally:
        db.rollback()
        db.delete(db.get(AuditTest, test.audit_test_id))
        db.commit()


def test_with_several_connections_the_one_that_has_the_table_supplies_it(monkeypatch):
    def connection(name):
        return SimpleNamespace(name=name)

    first, second = connection("db"), connection("files")

    def fake_fetch(conn, *, entity_name, field_names, limit):
        if conn is first:
            raise EntityNotHere("not mine")
        return [{"id": 1}]

    monkeypatch.setattr(direct_execution_service, "fetch_direct_records", fake_fetch)
    assert direct_execution_service._fetch_from_any([first, second], entity_name="t", field_names=["id"]) == [{"id": 1}]

    # A real failure on the connection that DOES have the table is what gets reported, not a later "not mine".
    boom = RuntimeError("the database is down")

    def fake_fetch_2(conn, *, entity_name, field_names, limit):
        if conn is first:
            raise boom
        raise EntityNotHere("not mine either")

    monkeypatch.setattr(direct_execution_service, "fetch_direct_records", fake_fetch_2)
    with pytest.raises(RuntimeError, match="down"):
        direct_execution_service._fetch_from_any([first, second], entity_name="t", field_names=["id"])

    # One connection: its error is raised unchanged.
    with pytest.raises(RuntimeError, match="down"):
        direct_execution_service._fetch_from_any([first], entity_name="t", field_names=["id"])


def test_relationship_measurement_over_rows_in_memory():
    m = data_source_service._measure_values(["a", "b", "b", None, "z"], ["a", "b", "c", "c"], as_text=False, capped=False)
    assert (m.child_distinct, m.matched_distinct, m.parent_distinct, m.parent_rows, m.capped) == (3, 2, 3, 4, False)
    # Differently typed columns compare as text, so 1 matches "1" — as the SQL path does.
    m = data_source_service._measure_values([1, 2], ["1", "3"], as_text=True, capped=False)
    assert m.matched_distinct == 1
    assert data_source_service._measure_values([{"x": 1}], [{"x": 1}], as_text=False, capped=False) is None  # nested values are not keys
    assert data_source_service._measure_values([1], [1], as_text=False, capped=True).capped is True


def test_nothing_is_left_behind_in_the_database():
    """Runs last in this file: the fixtures deleted their data sources; no connection or file of a test may remain."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        orphans = db.execute(text("select count(*) from data_files f left join data_connections c on c.connection_id = f.connection_id where c.connection_id is null")).scalar()
        leftovers = db.execute(text("select count(*) from data_sources where source_name = 'connector test source'")).scalar()
        assert orphans == 0 and leftovers == 0
    finally:
        db.close()


def test_managing_versions_never_loads_old_file_bytes_into_memory(db, world, monkeypatch):
    """Ten 25 MB versions must not be pulled into memory just to delete or count them: only ids are read."""
    from sqlalchemy import event

    monkeypatch.setattr(file_connector, "MAX_VERSIONS_PER_FILE", 2)
    connection = DataConnection(data_source_id=world.source.data_source_id, connection_mode="direct", db_type="file_upload", connection_status="pending")
    db.add(connection)
    db.commit()

    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "data_files.file_data" in statement:
            seen.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        stored = []
        for i in range(4):
            stored.append(file_connector.store_upload(db, connection=connection, file_name="p.csv", content_type="text/csv", data=f"a\n{i}\n".encode(), uploaded_by=None))
            db.commit()
        assert seen == []  # storing four versions, pruning to two, read no file bytes back
        # (a plain COUNT: ORM Query.count() wraps a sub-select that names every column, which is not a load of the bytes but would trip the detector)
        assert db.scalar(select(func.count()).select_from(DataFile).where(DataFile.connection_id == connection.connection_id)) == 2

        current = next(v for v in file_connector.list_versions(db, connection.connection_id) if v.is_current)
        assert file_connector.delete_version(db, connection_id=connection.connection_id, file_id=current.file_id) is True
        db.commit()
        assert seen == []  # deleting the current one and promoting the previous read no file bytes either
        assert [v.is_current for v in file_connector.list_versions(db, connection.connection_id)] == [True]

        # And the file that was just uploaded is not fetched back from the database to be parsed a second time.
        latest = file_connector.store_upload(db, connection=connection, file_name="q.csv", content_type="text/csv", data=b"a\n1\n", uploaded_by=None)
        db.commit()
        file_connector.tables_for(db, latest)
        assert seen == []
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)
        db.query(DataFile).filter(DataFile.connection_id == connection.connection_id).delete()
        db.commit()
