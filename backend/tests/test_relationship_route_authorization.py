"""
Route-level authorization for the relationship / profiling / join-resolution routes, with
real users and roles over real HTTP (FastAPI's TestClient): who may see, refresh and rule
on how a client's tables relate, and that one tenant can never reach another's.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.models.audit_test import AuditTest
from app.models.data_source import DataEntity, DataField, DataRelationship, DataSource
from app.models.organization import Organization
from app.models.rbac import Role, User, UserRole
from app.schemas.data_source import DiscoveredEntity, DiscoveredField, DiscoveryPayload
from app.services import data_source_service

client = TestClient(app)


def _user(db, *, role_name: str, org_id):
    user = User(
        organization_id=org_id, first_name="Rel", last_name="Test", email=f"rel-route-{uuid.uuid4().hex}@test.local",
        password_hash="not-a-real-hash", status="active",
    )
    db.add(user)
    db.flush()
    db.add(UserRole(user_id=user.user_id, role_id=db.query(Role).filter(Role.role_name == role_name).one().role_id))
    db.commit()
    db.refresh(user)
    return user


def _bearer(user):
    return {"Authorization": f"Bearer {create_access_token(user_id=user.user_id, organization_id=user.organization_id)}"}


@pytest.fixture
def world(db, test_org):
    """Org A (test_org) with a data source, two tables and one detected relationship; org B with its own audit test."""
    other = Organization(organization_name=f"Rel Route Other Org {uuid.uuid4().hex[:8]}", status="active")
    db.add(other)
    db.commit()
    source = DataSource(organization_id=test_org.organization_id, source_name="rel route test", source_type="postgresql", environment="cloud")
    db.add(source)
    db.commit()
    data_source_service.replace_discovery(
        db,
        data_source_id=source.data_source_id,
        payload=DiscoveryPayload(
            entities=[
                DiscoveredEntity(entity_name="users", fields=[DiscoveredField(field_name="user_id", data_type="TEXT", is_unique=True)]),
                DiscoveredEntity(entity_name="api_access", fields=[DiscoveredField(field_name="user_id", data_type="TEXT")]),
            ]
        ),
    )
    fields = {
        (db.get(DataEntity, f.entity_id).entity_name, f.field_name): f
        for f in db.query(DataField).join(DataEntity, DataEntity.entity_id == DataField.entity_id).filter(DataEntity.data_source_id == source.data_source_id)
    }
    edge = DataRelationship(
        data_source_id=source.data_source_id, child_field_id=fields[("api_access", "user_id")].field_id,
        parent_field_id=fields[("users", "user_id")].field_id, kind="inferred", containment=100.0, child_distinct=5, parent_distinct=8, parent_unique=True,
    )
    db.add(edge)
    other_test = AuditTest(organization_id=other.organization_id, test_name="other org test")
    db.add(other_test)
    db.commit()
    yield {"org": test_org, "other": other, "source": source, "edge": edge, "entity": fields[("api_access", "user_id")].entity_id, "other_test": other_test}
    db.delete(db.get(DataSource, source.data_source_id))
    db.delete(db.get(Organization, other.organization_id))
    db.commit()


@pytest.fixture
def read_only(db, test_org):
    user = _user(db, role_name="Read Only", org_id=test_org.organization_id)
    yield user
    db.delete(user)
    db.commit()


@pytest.fixture
def outsider(db, world):
    """A client user of the OTHER organization."""
    user = _user(db, role_name="Read Only", org_id=world["other"].organization_id)
    yield user
    db.delete(user)
    db.commit()


def test_read_only_user_cannot_refresh_profile_or_rule_on_relationships(read_only, world):
    h = _bearer(read_only)
    src = world["source"].data_source_id
    assert client.post(f"/api/v1/data-sources/{src}/relationships/refresh", headers=h).status_code == 403
    assert client.post(f"/api/v1/data-sources/{src}/profile", headers=h).status_code == 403
    assert client.post(f"/api/v1/entities/{world['entity']}/profile", headers=h).status_code == 403
    resp = client.patch(f"/api/v1/relationships/{world['edge'].relationship_id}", json={"status": "rejected"}, headers=h)
    assert resp.status_code == 403


def test_a_ruling_is_not_recorded_when_refused(db, read_only, world):
    client.patch(f"/api/v1/relationships/{world['edge'].relationship_id}", json={"status": "rejected"}, headers=_bearer(read_only))
    db.expire_all()
    assert db.get(DataRelationship, world["edge"].relationship_id).status == "detected"


def test_another_tenants_user_cannot_act_on_this_tenants_relationships(outsider, world):
    h = _bearer(outsider)
    src = world["source"].data_source_id
    assert client.post(f"/api/v1/data-sources/{src}/relationships/refresh", headers=h).status_code in (403, 404)
    assert client.patch(f"/api/v1/relationships/{world['edge'].relationship_id}", json={"status": "confirmed"}, headers=h).status_code in (403, 404)
    assert client.get(f"/api/v1/data-sources/entities/{world['entity']}/mapping-suggestions", headers=h).status_code == 403


def test_join_resolution_is_scoped_to_the_callers_organization(read_only, outsider, world):
    """Same URL shape, three callers: the org's own read-only user reaches the route (404: no such test here),
    and neither an outsider nor a cross-org URL gets through."""
    other_test = world["other_test"].audit_test_id
    # An outsider asking about THEIR OWN org's test is fine: it exists there.
    assert client.get(f"/api/v1/organizations/{world['other'].organization_id}/audit-tests/{other_test}/join-resolution", headers=_bearer(outsider)).status_code == 200
    # ...but the org-A user cannot read org B's, and org-B's test id under org A's URL is not found.
    assert client.get(f"/api/v1/organizations/{world['other'].organization_id}/audit-tests/{other_test}/join-resolution", headers=_bearer(read_only)).status_code == 403
    assert client.get(f"/api/v1/organizations/{world['org'].organization_id}/audit-tests/{other_test}/join-resolution", headers=_bearer(read_only)).status_code == 404


def test_mapping_suggestions_refuse_another_tenants_audit_test(read_only, world):
    """The control in view must belong to the same organization as the table."""
    h = _bearer(read_only)
    ok = client.get(f"/api/v1/data-sources/entities/{world['entity']}/mapping-suggestions", headers=h)
    assert ok.status_code == 200
    forged = client.get(
        f"/api/v1/data-sources/entities/{world['entity']}/mapping-suggestions", params={"audit_test_id": str(world["other_test"].audit_test_id)}, headers=h
    )
    assert forged.status_code == 404
    unknown = client.get(
        f"/api/v1/data-sources/entities/{world['entity']}/mapping-suggestions", params={"audit_test_id": str(uuid.uuid4())}, headers=h
    )
    assert unknown.status_code == 404
