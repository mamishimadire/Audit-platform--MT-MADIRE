import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from app.core.join_requirements import JoinRequirement
from app.core.join_resolution import (
    AMBIGUOUS,
    CONTRADICTED,
    UNRESOLVED,
    UNVERIFIED,
    VALID,
    Edge,
    find_paths,
    resolve_join,
)
from app.core.relationship_inference import (
    ColumnRef,
    Measurement,
    generate_candidates,
    name_affinity,
    score_inferred,
    worth_storing,
)
from app.core.schema_metadata import derive_column_constraints, normalize_foreign_keys
from app.models.data_source import (
    DataEntity,
    DataField,
    DataFieldConstraint,
    DataRelationship,
    DataSource,
)
from app.schemas.data_source import DiscoveredEntity, DiscoveredField, DiscoveredForeignKey, DiscoveryPayload
from app.services import data_source_service


def col(entity, name, *, data_type="TEXT", pk=False, unique=False, distinct_ratio=None, sample=0, null=None):
    return ColumnRef(
        field_id=f"{entity}.{name}", entity_id=entity, entity_name=entity, name=name, data_type=data_type,
        is_primary_key=pk, is_unique=unique, distinct_ratio=distinct_ratio, sample_size=sample, null_ratio=null,
    )


API_ACCESS = [col("api_access", "api_id"), col("api_access", "user_id"), col("api_access", "granted_at", data_type="TIMESTAMP")]
USERS = [col("users", "_id", data_type="objectid", pk=True), col("users", "user_id", unique=True), col("users", "username"), col("users", "status")]
REQ = JoinRequirement("missing_match", "api_access", "user_id", "user", "user_id")


def edge(child="api_access.user_id", parent="users.user_id", **kw):
    return Edge(child_field_id=child, parent_field_id=parent, **{"kind": "inferred", "containment": 100.0, "child_distinct": 12, "parent_distinct": 15, "parent_unique": True, **kw})


# ---- schema metadata --------------------------------------------------------------
def test_constraint_derivation():
    facts = derive_column_constraints(
        ["id", "email", "tenant_id", "status"],
        nullable_by_column={"id": True, "email": False, "tenant_id": True, "status": True},
        pk_columns=["id"],
        unique_constraints=[{"column_names": ["email"]}, {"column_names": ["tenant_id", "status"]}],
        indexes=[{"column_names": ["status"], "unique": False}],
    )
    assert facts["id"] == {"is_nullable": False, "is_unique": True, "is_indexed": True}  # a key is never nullable
    assert facts["email"]["is_unique"] and facts["email"]["is_indexed"]
    assert not facts["tenant_id"]["is_unique"]  # one member of a composite is not unique on its own
    assert facts["tenant_id"]["is_indexed"]  # ...but it leads the composite
    assert facts["status"] == {"is_nullable": True, "is_unique": False, "is_indexed": True}


def test_foreign_keys_normalise_and_none_means_not_reported():
    assert normalize_foreign_keys(None) is None
    assert normalize_foreign_keys([]) == []
    out = normalize_foreign_keys(
        [{"name": "fk", "constrained_columns": ["user_id"], "referred_table": "users", "referred_columns": ["user_id"]},
         {"name": "broken", "constrained_columns": ["a", "b"], "referred_table": "t", "referred_columns": ["a"]}]
    )
    assert out == [{"name": "fk", "columns": ["user_id"], "referred_table": "users", "referred_columns": ["user_id"]}]


# ---- inference: which pairs are worth measuring -----------------------------------------
def test_name_affinity():
    assert name_affinity(col("a", "user_id"), col("users", "user_id")) == 1.0
    assert name_affinity(col("a", "user_id"), col("users", "id", pk=True)) == 0.9
    assert name_affinity(col("user_roles", "role"), col("roles", "role_name")) == 0.6
    assert name_affinity(col("a", "created_by"), col("users", "user_id")) == 0.0


def test_candidates_need_a_key_like_parent_and_a_name_link():
    columns = [
        col("users", "user_id", unique=True), col("api_access", "user_id", distinct_ratio=0.4, sample=20), col("api_access", "created_by"),
        col("users", "status", distinct_ratio=0.2, sample=15),  # not key-like: never a parent
        col("api_access", "status"),
    ]
    pairs = {(c.child.field_id, c.parent.field_id) for c in generate_candidates(columns)}
    assert pairs == {("api_access.user_id", "users.user_id")}


def test_role_name_vs_role_id_is_left_to_the_measurement():
    columns = [
        col("roles", "role_id", unique=True), col("roles", "role_name", unique=True),
        col("user_roles", "role"), col("user_roles", "user_id"), col("users", "user_id", unique=True),
    ]
    pairs = {(c.child.field_id, c.parent.field_id) for c in generate_candidates(columns)}
    assert ("user_roles.role", "roles.role_name") in pairs and ("user_roles.user_id", "users.user_id") in pairs
    # role -> role_id passes on names alone; only the data can tell (ap_clerk vs R01).
    assert ("user_roles.role", "roles.role_id") in pairs
    role_id = next(c for c in generate_candidates(columns) if c.parent.name == "role_id")
    assert not worth_storing(Measurement(child_distinct=7, matched_distinct=0, parent_distinct=7), role_id.name_affinity)
    assert worth_storing(Measurement(child_distinct=7, matched_distinct=7, parent_distinct=7), 0.6)


def test_tiny_cardinality_is_not_a_relationship():
    assert not worth_storing(Measurement(child_distinct=1, matched_distinct=1, parent_distinct=15), 1.0)
    # identical names but the data disagrees: kept, as the evidence a mapping is wrong
    assert worth_storing(Measurement(child_distinct=20, matched_distinct=1, parent_distinct=15), 1.0)


def test_inferred_confidence_orders_by_evidence():
    cand = generate_candidates([col("users", "user_id", unique=True), col("api_access", "user_id")])[0]
    strong, _ = score_inferred(cand, Measurement(12, 12, 15))
    weak, _ = score_inferred(cand, Measurement(12, 6, 15))
    assert strong > weak


# ---- resolution ------------------------------------------------------------------------
def test_proven_relationship_is_valid():
    r = resolve_join(REQ, API_ACCESS, USERS, [edge()])
    assert r.verdict == VALID
    assert (r.left.name, r.right.name) == ("user_id", "user_id") and r.relationship == "inferred"


def test_declared_foreign_key_is_valid_without_any_measurement():
    r = resolve_join(REQ, API_ACCESS, USERS, [Edge("api_access.user_id", "users.user_id", "declared_fk")])
    assert r.verdict == VALID and r.relationship == "declared_fk"


def test_actor_column_is_never_the_subject():
    columns = API_ACCESS + [col("api_access", "created_by")]
    bad = edge(child="api_access.created_by")  # created_by also holds valid user ids
    r = resolve_join(REQ, columns, USERS, [edge(), bad])
    assert r.verdict == VALID and r.left.name == "user_id" and r.alternatives == []


def test_only_a_lookalike_actor_column_is_contradicted():
    owner_only = [col("api_access", "api_id"), col("api_access", "owner_user_id")]
    r = resolve_join(REQ, owner_only, USERS, [])
    assert r.verdict == CONTRADICTED and "different part" in r.reason


def test_no_column_for_the_requirement_is_unresolved():
    r = resolve_join(REQ, [col("api_access", "api_id")], USERS, [])
    assert r.verdict == UNRESOLVED


def test_values_that_do_not_match_are_contradicted_but_tiny_samples_are_not():
    r = resolve_join(REQ, API_ACCESS, USERS, [edge(containment=4.0, child_distinct=20, parent_distinct=40)])
    assert r.verdict == CONTRADICTED and "4%" in r.reason
    tiny = resolve_join(REQ, API_ACCESS, USERS, [edge(containment=0.0, child_distinct=2)])
    assert tiny.verdict == AMBIGUOUS  # two values prove nothing either way


def test_partial_match_needs_a_person():
    assert resolve_join(REQ, API_ACCESS, USERS, [edge(containment=62.0)]).verdict == AMBIGUOUS


def test_rejected_edge_is_contradicted_and_confirmed_is_valid():
    assert resolve_join(REQ, API_ACCESS, USERS, [edge(status="rejected")]).verdict == CONTRADICTED
    assert resolve_join(REQ, API_ACCESS, USERS, [edge(containment=40.0, status="confirmed")]).verdict == VALID


def test_no_evidence_at_all_is_unverified_not_a_demotion():
    assert resolve_join(REQ, API_ACCESS, USERS, []).verdict == UNVERIFIED


def test_two_equally_plausible_columns_are_ambiguous():
    # emp_id is an abbreviation of employee_id: as good a name match as the exact one.
    req = JoinRequirement("cross_match_condition", "employee", "employee_id", "user", "employee_id")
    employees = [col("employee", "employee_id"), col("employee", "emp_id")]
    users = [col("users", "user_id"), col("users", "employee_id")]
    r = resolve_join(req, employees, users, [])
    assert r.verdict == AMBIGUOUS and r.alternatives


def test_a_token_overlap_coincidence_is_not_a_rival_to_an_exact_name():
    req = JoinRequirement("missing_match", "data_access", "access_id", "audit_logs", "entity_id")
    logs = [col("audit_logs", "entity_id"), col("audit_logs", "company_id")]
    r = resolve_join(req, [col("data_access", "access_id")], logs, [])
    assert r.verdict == UNVERIFIED and r.alternatives == []


def test_zero_matches_on_a_tiny_sample_is_worded_honestly():
    r = resolve_join(REQ, API_ACCESS, USERS, [edge(containment=0.0, child_distinct=3, parent_distinct=8)])
    assert r.verdict == AMBIGUOUS and "None of the 3" in r.reason and "partial" not in r.reason


def test_prepared_by_requirement_is_met_by_prepared_by_not_approved_by():
    req = JoinRequirement("missing_match", "journal_entries", "prepared_by", "user", "user_id")
    entries = [col("journal_entries", "journal_id"), col("journal_entries", "prepared_by"), col("journal_entries", "approved_by")]
    e = Edge("journal_entries.prepared_by", "users.user_id", "declared_fk")
    r = resolve_join(req, entries, USERS, [e])
    assert r.verdict == VALID and r.left.name == "prepared_by"


# ---- multi-hop path ---------------------------------------------------------------------
def test_path_through_a_bridge_table():
    columns = API_ACCESS + USERS + [
        col("user_roles", "user_id"), col("user_roles", "role"), col("roles", "role_id", unique=True), col("roles", "role_name", unique=True),
    ]
    edges = [
        edge(),
        Edge("user_roles.user_id", "users.user_id", "inferred", containment=100.0, child_distinct=15, parent_unique=True),
        Edge("user_roles.role", "roles.role_name", "inferred", containment=100.0, child_distinct=7, parent_unique=True),
        Edge("user_roles.role", "roles.role_id", "inferred", containment=0.0, child_distinct=7, parent_unique=True),
    ]
    paths = {(p.from_entity, p.to_entity): p for p in find_paths(["api_access", "users", "roles"], columns, edges)}
    users_to_roles = paths[("users", "roles")]
    assert [(s.from_column, s.to_column) for s in users_to_roles.steps] == [
        ("users.user_id", "user_roles.user_id"), ("user_roles.role", "roles.role_name"),
    ]
    assert not users_to_roles.executed
    assert len(paths[("api_access", "roles")].steps) == 3
    assert all("role_id" not in s.to_column for p in paths.values() for s in p.steps)  # the 0% edge is never a path


# ---- SQL measurement + end-to-end inference on a real (SQLite) client database ----------------
@pytest.fixture
def client_db(tmp_path):
    path = tmp_path / "client.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (user_id TEXT PRIMARY KEY, username TEXT, status TEXT)"))
        conn.execute(text("CREATE TABLE api_access (api_id TEXT, user_id TEXT, created_by TEXT)"))
        conn.execute(text("CREATE TABLE roles (role_id TEXT PRIMARY KEY, role_name TEXT UNIQUE)"))
        conn.execute(text("CREATE TABLE user_roles (user_id TEXT REFERENCES users(user_id), role TEXT)"))
        for i in range(1, 9):
            conn.execute(text("INSERT INTO users VALUES (:u, :n, 'active')"), {"u": f"U{i:03d}", "n": f"n{i}"})
        for i in range(1, 7):
            conn.execute(text("INSERT INTO api_access VALUES ('API001', :u, 'someone')"), {"u": f"U{i:03d}"})
            conn.execute(text("INSERT INTO user_roles VALUES (:u, :r)"), {"u": f"U{i:03d}", "r": f"role_{i}"})
        for i in range(1, 7):
            conn.execute(text("INSERT INTO roles VALUES (:i, :n)"), {"i": f"R{i:02d}", "n": f"role_{i}"})
    engine.dispose()
    return path


def test_sqlite_schema_discovery_reads_declared_metadata(client_db, monkeypatch):
    monkeypatch.setattr(data_source_service, "_build_direct_engine", lambda c: create_engine(f"sqlite:///{client_db}"))
    captured = {}
    monkeypatch.setattr(data_source_service, "replace_discovery", lambda db, **k: captured.update(k) or [])
    data_source_service.discover_direct_connection_schema(None, connection=SimpleNamespace(db_type="sqlite", data_source_id=uuid.uuid4()))
    entities = {e.entity_name: e for e in captured["payload"].entities}
    assert [(fk.columns, fk.referred_table) for fk in entities["user_roles"].foreign_keys] == [(["user_id"], "users")]
    assert entities["users"].foreign_keys == []  # reported: none
    fields = {f.field_name: f for f in entities["users"].fields}
    assert fields["user_id"].is_primary_key and fields["user_id"].is_unique
    assert entities["roles"].fields[1].is_unique  # UNIQUE constraint on role_name


def test_one_failing_catalog_call_never_fails_discovery(client_db, monkeypatch):
    monkeypatch.setattr(data_source_service, "_build_direct_engine", lambda c: create_engine(f"sqlite:///{client_db}"))
    real = data_source_service._safe_inspector_call

    def flaky(call, table):
        if getattr(call, "__name__", "") == "get_foreign_keys":
            return None
        return real(call, table)

    monkeypatch.setattr(data_source_service, "_safe_inspector_call", flaky)
    captured = {}
    monkeypatch.setattr(data_source_service, "replace_discovery", lambda db, **k: captured.update(k) or [])
    data_source_service.discover_direct_connection_schema(None, connection=SimpleNamespace(db_type="sqlite", data_source_id=uuid.uuid4()))
    assert all(e.foreign_keys is None for e in captured["payload"].entities)  # "not reported", not "none exist"


@pytest.fixture
def stored_source(db, test_org):
    source = DataSource(organization_id=test_org.organization_id, source_name="relationship test", source_type="postgresql", environment="cloud")
    db.add(source)
    db.commit()
    yield source
    db.delete(db.get(DataSource, source.data_source_id))
    db.commit()


def _payload_from_sqlite(client_db):
    engine = create_engine(f"sqlite:///{client_db}")
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(engine)
    entities = []
    for table in inspector.get_table_names():
        pk = set(inspector.get_pk_constraint(table).get("constrained_columns") or [])
        cols = inspector.get_columns(table)
        facts = derive_column_constraints(
            [c["name"] for c in cols], nullable_by_column={c["name"]: c.get("nullable") for c in cols}, pk_columns=pk,
            unique_constraints=inspector.get_unique_constraints(table), indexes=inspector.get_indexes(table),
        )
        entities.append(
            DiscoveredEntity(
                entity_name=table,
                fields=[
                    DiscoveredField(field_name=c["name"], data_type=str(c["type"]), is_primary_key=c["name"] in pk, **facts[c["name"]])
                    for c in cols
                ],
                foreign_keys=[DiscoveredForeignKey(**fk) for fk in normalize_foreign_keys(inspector.get_foreign_keys(table))],
            )
        )
    engine.dispose()
    return DiscoveryPayload(entities=entities)


def test_declared_relationships_are_stored_and_survive_rediscovery(db, stored_source, client_db):
    payload = _payload_from_sqlite(client_db)
    data_source_service.replace_discovery(db, data_source_id=stored_source.data_source_id, payload=payload)

    def edges():
        db.expire_all()
        return db.query(DataRelationship).filter(DataRelationship.data_source_id == stored_source.data_source_id).all()

    declared = edges()
    assert len(declared) == 1 and declared[0].kind == "declared_fk" and declared[0].parent_unique is True
    constraints = db.query(DataFieldConstraint).join(DataField, DataField.field_id == DataFieldConstraint.field_id).join(
        DataEntity, DataEntity.entity_id == DataField.entity_id
    ).filter(DataEntity.data_source_id == stored_source.data_source_id, DataField.field_name == "role_name").one()
    assert constraints.is_unique

    # An auditor's ruling is never overwritten by the next discovery.
    declared[0].status = "confirmed"
    db.commit()
    data_source_service.replace_discovery(db, data_source_id=stored_source.data_source_id, payload=payload)
    assert [(e.kind, e.status) for e in edges()] == [("declared_fk", "confirmed")]

    # A discovery that could not read foreign keys (None) must not erase what is known.
    silent = DiscoveryPayload(entities=[e.model_copy(update={"foreign_keys": None}) for e in payload.entities])
    data_source_service.replace_discovery(db, data_source_id=stored_source.data_source_id, payload=silent)
    assert len(edges()) == 1


def test_inference_finds_the_real_relationships_and_rejects_role_id(db, stored_source, client_db, monkeypatch):
    payload = _payload_from_sqlite(client_db)
    # Drop the declared FK so everything below is inferred from data alone.
    payload = DiscoveryPayload(entities=[e.model_copy(update={"foreign_keys": None}) for e in payload.entities])
    data_source_service.replace_discovery(db, data_source_id=stored_source.data_source_id, payload=payload)

    monkeypatch.setattr(data_source_service, "_connected_direct_connections", lambda db_, sid: [SimpleNamespace(db_type="sqlite", db=None)])
    monkeypatch.setattr(data_source_service, "_build_direct_engine", lambda c: create_engine(f"sqlite:///{client_db}"))
    count = data_source_service.infer_relationships_for_source(db, data_source_id=stored_source.data_source_id)
    assert count and count >= 3

    names = {}
    for f in db.query(DataField).join(DataEntity, DataEntity.entity_id == DataField.entity_id).filter(DataEntity.data_source_id == stored_source.data_source_id):
        entity = db.get(DataEntity, f.entity_id)
        names[f.field_id] = f"{entity.entity_name}.{f.field_name}"
    found = {(names[e.child_field_id], names[e.parent_field_id]): e for e in db.query(DataRelationship).filter(DataRelationship.data_source_id == stored_source.data_source_id, DataRelationship.kind == "inferred")}
    assert found[("api_access.user_id", "users.user_id")].containment == 100.0
    assert found[("user_roles.role", "roles.role_name")].containment == 100.0
    assert ("user_roles.role", "roles.role_id") not in found  # ap_clerk-style names never match R01-style ids
    assert ("api_access.created_by", "users.user_id") not in found  # no name link, never even measured

    # Re-running replaces its own edges; it never duplicates them.
    again = data_source_service.infer_relationships_for_source(db, data_source_id=stored_source.data_source_id)
    assert again == count


# ---- the service: stored graph -> a control's join report ----------------------------------
def _seed_api_access_source(db, source):
    data_source_service.replace_discovery(
        db,
        data_source_id=source.data_source_id,
        payload=DiscoveryPayload(
            entities=[
                DiscoveredEntity(entity_name="api_access", fields=[DiscoveredField(field_name=n, data_type="TEXT") for n in ("api_id", "user_id", "created_by")]),
                DiscoveredEntity(entity_name="users", fields=[DiscoveredField(field_name="user_id", data_type="TEXT", is_primary_key=True, is_unique=True), DiscoveredField(field_name="status", data_type="TEXT")]),
                DiscoveredEntity(entity_name="user_roles", fields=[DiscoveredField(field_name="user_id", data_type="TEXT"), DiscoveredField(field_name="role", data_type="TEXT")]),
                DiscoveredEntity(entity_name="roles", fields=[DiscoveredField(field_name="role_name", data_type="TEXT", is_unique=True)]),
            ]
        ),
    )
    ids = {}
    for entity in db.query(DataEntity).filter(DataEntity.data_source_id == source.data_source_id):
        ids[entity.entity_name] = entity.entity_id
    fields = {
        (name, f.field_name): f.field_id
        for name, eid in ids.items()
        for f in db.query(DataField).filter(DataField.entity_id == eid)
    }
    return ids, fields


def _add_edge(db, source, fields, child, parent, containment, distinct=12, parent_distinct=15):
    db.add(
        DataRelationship(
            data_source_id=source.data_source_id, child_field_id=fields[child], parent_field_id=fields[parent], kind="inferred",
            containment=containment, child_distinct=distinct, parent_distinct=parent_distinct, parent_unique=True, cardinality="many_to_one", confidence=90.0,
        )
    )
    db.commit()


_API002_RULE = {"rule_type": "missing_match", "primary_object": "api_access", "secondary_object": "user", "join_field": "user_id"}


def test_join_report_for_a_control_and_the_bridge_path(db, stored_source):
    from app.services.join_resolution_service import build_join_report

    ids, fields = _seed_api_access_source(db, stored_source)
    _add_edge(db, stored_source, fields, ("api_access", "user_id"), ("users", "user_id"), 100.0)
    _add_edge(db, stored_source, fields, ("user_roles", "user_id"), ("users", "user_id"), 100.0)
    _add_edge(db, stored_source, fields, ("user_roles", "role"), ("roles", "role_name"), 100.0)
    object_entities = {"api_access": ids["api_access"], "user": ids["users"], "role": ids["roles"]}

    report = build_join_report(db, audit_test_id=uuid.uuid4(), rule_definition=_API002_RULE, object_entities=object_entities)
    assert [(j.requires_left, j.requires_right, j.verdict) for j in report.joins] == [("api_access.user_id", "user.user_id", "valid")]
    assert (report.joins[0].left.table, report.joins[0].left.column) == ("api_access", "user_id")
    assert report.blocking is False
    users_to_roles = next(p for p in report.paths if (p.from_table, p.to_table) == ("users", "roles"))
    assert [(s.from_column, s.to_column) for s in users_to_roles.steps] == [
        ("users.user_id", "user_roles.user_id"), ("user_roles.role", "roles.role_name"),
    ]
    assert users_to_roles.executed is False


def test_contradicted_join_blocks_and_an_auditor_ruling_is_respected(db, stored_source):
    from app.services.join_resolution_service import build_join_report

    ids, fields = _seed_api_access_source(db, stored_source)
    _add_edge(db, stored_source, fields, ("api_access", "user_id"), ("users", "user_id"), 4.0, distinct=20, parent_distinct=40)
    object_entities = {"api_access": ids["api_access"], "user": ids["users"]}

    report = build_join_report(db, audit_test_id=uuid.uuid4(), rule_definition=_API002_RULE, object_entities=object_entities)
    assert report.joins[0].verdict == "contradicted" and report.blocking is True

    edge = db.query(DataRelationship).filter(DataRelationship.data_source_id == stored_source.data_source_id).one()
    edge.status = "confirmed"  # the auditor knows the data is a partial export and vouches for the relationship
    db.commit()
    again = build_join_report(db, audit_test_id=uuid.uuid4(), rule_definition=_API002_RULE, object_entities=object_entities)
    assert again.joins[0].verdict == "valid" and again.blocking is False
    assert again.joins[0].relationship_id == edge.relationship_id


def test_unbound_table_is_unresolved_not_a_block(db, stored_source):
    from app.services.join_resolution_service import build_join_report

    ids, _ = _seed_api_access_source(db, stored_source)
    report = build_join_report(db, audit_test_id=uuid.uuid4(), rule_definition=_API002_RULE, object_entities={"api_access": ids["api_access"]})
    assert report.joins[0].verdict == "unresolved" and report.blocking is False


def test_suggestions_for_a_flagged_join_key_are_capped_below_auto_accept(db, stored_source, monkeypatch):
    from app.schemas.data_mapping import JoinColumnOut, JoinReportOut, JoinResolutionOut
    from app.services import join_resolution_service, mapping_service

    ids, fields = _seed_api_access_source(db, stored_source)
    flagged = fields[("api_access", "user_id")]
    report = JoinReportOut(
        joins=[
            JoinResolutionOut(
                primitive="missing_match", requires_left="api_access.user_id", requires_right="user.user_id", verdict="contradicted",
                reason="Only 4% of the values exist on the other side.", left=JoinColumnOut(field_id=flagged, table="api_access", column="user_id"),
            )
        ],
        paths=[],
        blocking=True,
    )
    monkeypatch.setattr(join_resolution_service, "build_join_report", lambda *a, **k: report)

    plain = {s.field_name: s for s in mapping_service.suggest_mappings_for_entity(db, entity_id=ids["api_access"])}
    judged = {s.field_name: s for s in mapping_service.suggest_mappings_for_entity(db, entity_id=ids["api_access"], audit_test_id=uuid.uuid4())}
    assert plain["user_id"].confidence_score >= 90 and plain["user_id"].relationship_reason is None
    assert judged["user_id"].confidence_score < 90 and "4%" in judged["user_id"].relationship_reason
    assert judged["api_id"].confidence_score == plain["api_id"].confidence_score  # other columns untouched


# ---- precision: what must NOT become a relationship (found on the live demo data) ----------
def test_flags_dates_and_amounts_are_never_keys():
    columns = [
        col("network_connections", "approved", data_type="boolean", distinct_ratio=1.0, sample=2),
        col("user_access_changes", "approved", data_type="boolean"),
        col("customers", "credit_limit", data_type="double", distinct_ratio=1.0, sample=4),
        col("credit_approvals", "credit_limit", data_type="double"),
        col("change_requests", "requested_at", data_type="date", distinct_ratio=1.0, sample=3),
        col("privacy_requests", "requested_at", data_type="date"),
    ]
    assert generate_candidates(columns) == []


def test_a_two_row_table_cannot_vouch_that_a_column_is_a_key():
    busy = dict(distinct_ratio=0.3, sample=40)  # a child that is not itself key-like
    columns = [col("api_access", "user_id", distinct_ratio=1.0, sample=2), col("audit", "user_id", **busy)]
    assert generate_candidates(columns) == []
    columns = [col("users", "user_id", distinct_ratio=1.0, sample=15), col("audit", "user_id", **busy)]
    assert len(generate_candidates(columns)) == 1
    # No profile at all: the measurement decides whether the parent really is a key.
    assert not Measurement(child_distinct=5, matched_distinct=5, parent_distinct=2, parent_rows=2).parent_is_key
    assert not Measurement(child_distinct=5, matched_distinct=5, parent_distinct=8, parent_rows=20).parent_is_key
    assert Measurement(child_distinct=5, matched_distinct=5, parent_distinct=15, parent_rows=15).parent_is_key


def test_a_status_column_is_not_key_shaped_even_if_every_sampled_value_differs():
    columns = [col("a", "status", distinct_ratio=1.0, sample=5), col("b", "status")]
    assert generate_candidates(columns) == []


def test_the_table_the_column_is_named_after_wins_the_candidate_cap():
    columns = [col("audit", "user_id")] + [col(f"t{i}", "user_id", distinct_ratio=1.0, sample=3) for i in range(8)] + [
        col("system_users", "user_id", distinct_ratio=1.0, sample=15)
    ]
    parents = [c.parent.entity_name for c in generate_candidates(columns) if c.child.entity_name == "audit"]
    assert "system_users" in parents and len(parents) <= 5


def test_a_bigger_set_not_fitting_in_a_smaller_one_is_no_evidence():
    """All 15 users are not inside a 2-row access table: that reverse measurement
    must neither prove nor contradict api_access.user_id -> users.user_id."""
    reverse = edge(child="users.user_id", parent="api_access.user_id", containment=13.0, child_distinct=15, parent_distinct=2)
    forward = edge()
    r = resolve_join(REQ, API_ACCESS, USERS, [reverse, forward])
    assert r.verdict == VALID and r.containment == 100.0
    only_reverse = resolve_join(REQ, API_ACCESS, USERS, [reverse])
    assert only_reverse.verdict == UNVERIFIED


def test_a_partial_overlap_never_blocks_a_missing_match_test():
    """Unmatched rows are the exceptions the control looks for, not a broken join."""
    r = resolve_join(REQ, API_ACCESS, USERS, [edge(containment=40.0, child_distinct=20, parent_distinct=40)])
    assert r.verdict == AMBIGUOUS


# ---- the Gateway's copy of the catalog logic must never drift from the platform's ----------------
def test_gateway_and_platform_schema_metadata_never_drift():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "gateway" / "gateway" / "schema_metadata.py"
    spec = importlib.util.spec_from_file_location("gateway_schema_metadata_under_test", path)
    gateway = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gateway)

    cases = [
        dict(column_names=["id", "email", "tenant_id", "status"], nullable_by_column={"id": True, "email": False, "tenant_id": True, "status": None},
             pk_columns=["id"], unique_constraints=[{"column_names": ["email"]}, {"column_names": ["tenant_id", "status"]}],
             indexes=[{"column_names": ["status"], "unique": False}, {"column_names": ["email"], "unique": True}]),
        dict(column_names=["a", "b"], nullable_by_column=None, pk_columns=["a", "b"], unique_constraints=None, indexes=None),
        dict(column_names=["x"], nullable_by_column={"x": True}, pk_columns=[], unique_constraints=[], indexes=[{"column_names": ["x"], "unique": True}]),
    ]
    for case in cases:
        names = case.pop("column_names")
        assert gateway.derive_column_constraints(names, **case) == derive_column_constraints(names, **case)
    fks = [
        None, [],
        [{"name": "fk", "constrained_columns": ["user_id"], "referred_table": "users", "referred_columns": ["user_id"]},
         {"name": "bad", "constrained_columns": ["a", "b"], "referred_table": "t", "referred_columns": ["a"]},
         {"name": "empty", "constrained_columns": [], "referred_table": "t", "referred_columns": []}],
    ]
    for fk in fks:
        assert gateway.normalize_foreign_keys(fk) == normalize_foreign_keys(fk)


def test_a_control_requirement_is_measured_even_when_the_names_share_nothing(db, stored_source, tmp_path, monkeypatch):
    """GL-002: journal_entries.prepared_by must line up with user.user_id. No name
    connects them, so name-driven inference never compares them; the control
    itself says which pair to measure."""
    path = tmp_path / "gl.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (user_id TEXT, username TEXT)"))
        conn.execute(text("CREATE TABLE journal_entries (journal_id TEXT, prepared_by TEXT)"))
        for i in range(1, 9):
            conn.execute(text("INSERT INTO users VALUES (:u, :n)"), {"u": f"U{i}", "n": f"n{i}"})
        for i in range(1, 7):
            conn.execute(text("INSERT INTO journal_entries VALUES (:j, :u)"), {"j": f"J{i}", "u": f"U{i}"})
    engine.dispose()
    data_source_service.replace_discovery(
        db,
        data_source_id=stored_source.data_source_id,
        payload=DiscoveryPayload(
            entities=[
                DiscoveredEntity(entity_name="users", fields=[DiscoveredField(field_name="user_id", data_type="TEXT"), DiscoveredField(field_name="username", data_type="TEXT")]),
                DiscoveredEntity(entity_name="journal_entries", fields=[DiscoveredField(field_name="journal_id", data_type="TEXT"), DiscoveredField(field_name="prepared_by", data_type="TEXT")]),
            ]
        ),
    )
    columns = data_source_service._columns_for_inference(db, stored_source.data_source_id)
    assert data_source_service.generate_candidates(columns) == []  # names alone would never pair these

    by_name = {(c.entity_name, c.name): c for c in columns}
    from app.core.relationship_inference import Candidate

    driven = [
        Candidate(by_name[("journal_entries", "prepared_by")], by_name[("users", "user_id")], 1.0, False, requirement_driven=True),
        Candidate(by_name[("users", "user_id")], by_name[("journal_entries", "prepared_by")], 1.0, False, requirement_driven=True),
    ]
    monkeypatch.setattr(data_source_service, "_requirement_candidates", lambda *a, **k: driven)
    monkeypatch.setattr(data_source_service, "_connected_direct_connections", lambda db_, sid: [SimpleNamespace(db_type="sqlite")])
    monkeypatch.setattr(data_source_service, "_build_direct_engine", lambda c: create_engine(f"sqlite:///{path}"))
    assert data_source_service.infer_relationships_for_source(db, data_source_id=stored_source.data_source_id) == 2

    edges = {(e.child_field_id, e.parent_field_id): e for e in db.query(DataRelationship).filter(DataRelationship.data_source_id == stored_source.data_source_id)}
    forward = edges[(by_name[("journal_entries", "prepared_by")].field_id and uuid.UUID(by_name[("journal_entries", "prepared_by")].field_id), uuid.UUID(by_name[("users", "user_id")].field_id))]
    assert forward.containment == 100.0 and forward.child_distinct == 6 and forward.parent_distinct == 8


def test_requirement_candidates_come_from_real_controls_and_bindings(db, test_org, stored_source):
    """Runs the real path: control -> its rule template -> its bound tables ->
    the exact column pairs the rule needs joined (GL-002-style: prepared_by vs
    user_id, which share no name)."""
    import json

    from app.models.control_library import ControlLibraryEntry, ControlRuleTemplate, ControlTableBinding
    from app.models.risk_control import Control

    data_source_service.replace_discovery(
        db,
        data_source_id=stored_source.data_source_id,
        payload=DiscoveryPayload(
            entities=[
                DiscoveredEntity(entity_name="users", fields=[DiscoveredField(field_name="user_id", data_type="TEXT"), DiscoveredField(field_name="created_by", data_type="TEXT")]),
                DiscoveredEntity(entity_name="journal_entries", fields=[DiscoveredField(field_name="journal_id", data_type="TEXT"), DiscoveredField(field_name="prepared_by", data_type="TEXT"), DiscoveredField(field_name="approved_by", data_type="TEXT")]),
            ]
        ),
    )
    entity_ids = {e.entity_name: e.entity_id for e in db.query(DataEntity).filter(DataEntity.data_source_id == stored_source.data_source_id)}
    library = ControlLibraryEntry(
        domain="Test", control_code=f"T-{uuid.uuid4().hex[:8]}", control_name="join test", audit_procedure="x", required_tables=["journal_entries", "users"]
    )
    db.add(library)
    db.flush()
    db.add(
        ControlRuleTemplate(
            control_library_id=library.control_library_id, rule_name="prepared by a real user",
            rule_definition=json.dumps({"rule_type": "missing_match", "primary_object": "journal_entries", "secondary_object": "user", "join_field": "prepared_by", "secondary_join_field": "user_id"}),
        )
    )
    control = Control(organization_id=test_org.organization_id, control_library_id=library.control_library_id, control_code=library.control_code, control_name="join test", status="pending_mapping")
    db.add(control)
    db.flush()
    for table in ("journal_entries", "users"):
        db.add(
            ControlTableBinding(
                organization_id=test_org.organization_id, control_id=control.control_id, canonical_table_name=table,
                data_source_id=stored_source.data_source_id, entity_id=entity_ids[table], status="bound",
            )
        )
    db.commit()
    try:
        columns = data_source_service._columns_for_inference(db, stored_source.data_source_id)
        pairs = {
            (c.child.entity_name, c.child.name, c.parent.entity_name, c.parent.name)
            for c in data_source_service._requirement_candidates(db, stored_source.data_source_id, columns)
        }
        # Exactly the required pair, both directions; approved_by and created_by are never paired.
        assert pairs == {
            ("journal_entries", "prepared_by", "users", "user_id"),
            ("users", "user_id", "journal_entries", "prepared_by"),
        }
    finally:
        db.delete(control)
        db.query(ControlRuleTemplate).filter(ControlRuleTemplate.control_library_id == library.control_library_id).delete()
        db.delete(library)
        db.commit()
