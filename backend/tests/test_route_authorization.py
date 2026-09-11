"""
Route-level (real HTTP, via FastAPI's TestClient) authorization tests —
complementing test_sod_workflows.py's service-layer tests, which call
Python functions directly and never exercise the require_permissions(...)
FastAPI dependency or enforce_same_organization at the actual route.

These specifically prove: a Read Only user (zero permissions) gets a real
403 from the API when attempting a mutation, and a platform user scoped
to Org A gets a real 403 reading Org B's data via a direct HTTP request —
i.e. "bypassing the UI and hitting the API directly" is still blocked,
not just "the button doesn't render."
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.models.rbac import Role, User, UserRole

client = TestClient(app)


def _make_client_user(db, *, role_name: str, org_id: uuid.UUID) -> User:
    user = User(
        organization_id=org_id,
        first_name="Route",
        last_name="Test",
        email=f"route-test-{uuid.uuid4().hex}@test.local",
        password_hash="not-a-real-hash",
        status="active",
    )
    db.add(user)
    db.flush()
    role = db.query(Role).filter(Role.role_name == role_name).one()
    db.add(UserRole(user_id=user.user_id, role_id=role.role_id))
    db.commit()
    db.refresh(user)
    return user


def _bearer(user_id: uuid.UUID, org_id: uuid.UUID | None) -> dict:
    token = create_access_token(user_id=user_id, organization_id=org_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def read_only_user(db, test_org):
    user = _make_client_user(db, role_name="Read Only", org_id=test_org.organization_id)
    yield user
    db.delete(user)
    db.commit()


def test_read_only_user_cannot_create_control_table_binding(db, test_org, read_only_user):
    control = _active_control_row(db, test_org.organization_id)
    resp = client.post(
        f"/api/v1/organizations/{test_org.organization_id}/controls/{control.control_id}/table-bindings",
        json={"canonical_table_name": "system_users", "data_source_id": str(uuid.uuid4()), "entity_id": str(uuid.uuid4())},
        headers=_bearer(read_only_user.user_id, test_org.organization_id),
    )
    assert resp.status_code == 403


def test_read_only_user_cannot_request_control_deactivation(db, test_org, read_only_user):
    control = _active_control_row(db, test_org.organization_id)
    resp = client.post(
        f"/api/v1/organizations/{test_org.organization_id}/controls/{control.control_id}/deactivation/request",
        json={"reason": "trying anyway"},
        headers=_bearer(read_only_user.user_id, test_org.organization_id),
    )
    assert resp.status_code == 403


def test_read_only_user_cannot_reject_a_test_rule(db, test_org, read_only_user):
    resp = client.post(
        f"/api/v1/test-rules/{uuid.uuid4()}/reject",
        json={"reason": "trying anyway"},
        headers=_bearer(read_only_user.user_id, test_org.organization_id),
    )
    # 403 (no permission) must fire before the route ever looks up the
    # (nonexistent) rule and could otherwise 404 first.
    assert resp.status_code == 403


def test_read_only_user_can_still_view_controls(db, test_org, read_only_user):
    """The fix must not have overshot into blocking legitimate reads."""
    resp = client.get(
        f"/api/v1/organizations/{test_org.organization_id}/controls",
        headers=_bearer(read_only_user.user_id, test_org.organization_id),
    )
    assert resp.status_code == 200


def test_platform_user_without_scope_cannot_read_a_different_orgs_controls(db, test_org, maker_user):
    """maker_user (Auditor, platform-scoped) is scoped to test_org only —
    a direct API request for a different org's data must 403, not 200 with
    an empty/filtered list and not silently succeed because the UI would
    never have pointed them there."""
    from app.models.organization import Organization

    other_org = Organization(organization_name=f"Route Test Other Org {uuid.uuid4().hex[:8]}", status="active")
    db.add(other_org)
    db.commit()
    db.refresh(other_org)
    try:
        resp = client.get(
            f"/api/v1/organizations/{other_org.organization_id}/controls",
            headers=_bearer(maker_user.user_id, None),
        )
        assert resp.status_code == 403
    finally:
        db.delete(other_org)
        db.commit()


def test_unauthenticated_request_is_rejected(test_org):
    resp = client.get(f"/api/v1/organizations/{test_org.organization_id}/controls")
    assert resp.status_code == 401


def _active_control_row(db, org_id: uuid.UUID):
    from app.models.risk_control import Control

    control = Control(organization_id=org_id, control_name="Route test control", status="active")
    db.add(control)
    db.commit()
    db.refresh(control)
    return control
