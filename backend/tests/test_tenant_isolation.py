from app.models.rbac import Role, User, UserRole
from app.schemas.organization import OrganizationCreate
from app.services.auth_service import activate_pending_user
from app.services.organization_service import create_organization_with_admin
from tests.test_auth import _make_system_admin


def _onboard_and_activate(client, db_session, admin_user_id, org_name: str, email: str) -> str:
    """Returns a bearer token for the newly activated organization admin."""
    organization, pending_user, temp_password = create_organization_with_admin(
        db_session,
        payload=OrganizationCreate(
            organization_name=org_name,
            primary_admin_first_name="Test",
            primary_admin_last_name="Admin",
            primary_admin_email=email,
        ),
        created_by_user_id=admin_user_id,
    )
    activate_pending_user(
        db_session, email=pending_user.email, temporary_password=temp_password, new_password="a-strong-chosen-password"
    )
    login = client.post("/api/v1/auth/login", data={"username": email, "password": "a-strong-chosen-password"})
    assert login.status_code == 200
    return login.json()["access_token"], organization.organization_id


def test_user_cannot_read_another_organization(client, db_session):
    system_admin = _make_system_admin(db_session)

    token_a, org_a_id = _onboard_and_activate(
        client, db_session, system_admin.user_id, "Org A Pty Ltd", "admin-a@org-a.example"
    )
    _token_b, org_b_id = _onboard_and_activate(
        client, db_session, system_admin.user_id, "Org B Pty Ltd", "admin-b@org-b.example"
    )

    # Org A's admin must be able to read their own organization...
    own_org = client.get(f"/api/v1/organizations/{org_a_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert own_org.status_code == 200

    # ...but never Org B's, even though both are valid, existing organizations.
    cross_org = client.get(f"/api/v1/organizations/{org_b_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert cross_org.status_code == 403

    cross_org_users = client.get(
        f"/api/v1/organizations/{org_b_id}/users", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert cross_org_users.status_code == 403


def test_organization_list_is_scoped_to_caller(client, db_session):
    system_admin = _make_system_admin(db_session)
    token_a, org_a_id = _onboard_and_activate(
        client, db_session, system_admin.user_id, "Solo Org", "admin-solo@org-solo.example"
    )

    listing = client.get("/api/v1/organizations", headers={"Authorization": f"Bearer {token_a}"})
    assert listing.status_code == 200
    ids = {row["organization_id"] for row in listing.json()}
    assert ids == {str(org_a_id)}
