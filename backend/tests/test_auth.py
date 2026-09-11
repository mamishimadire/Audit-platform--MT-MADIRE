import uuid

from app.core.security import hash_password
from app.models.rbac import Role, User, UserRole
from app.schemas.organization import OrganizationCreate
from app.services.organization_service import create_organization_with_admin


def _make_system_admin(db_session) -> User:
    role = db_session.query(Role).filter_by(role_name="Platform Super Admin").one()
    admin = User(
        organization_id=None,
        first_name="Ada",
        last_name="Admin",
        email=f"admin-{uuid.uuid4()}@example.com",
        password_hash=hash_password("a-strong-bootstrap-password"),
        status="active",
    )
    db_session.add(admin)
    db_session.flush()
    db_session.add(UserRole(user_id=admin.user_id, role_id=role.role_id))
    db_session.commit()
    return admin


def test_login_rejects_wrong_password(client, db_session):
    admin = _make_system_admin(db_session)
    response = client.post("/api/v1/auth/login", data={"username": admin.email, "password": "wrong-password"})
    assert response.status_code == 401


def test_onboarding_activation_and_login_flow(client, db_session):
    admin = _make_system_admin(db_session)

    organization, pending_user, temporary_password = create_organization_with_admin(
        db_session,
        payload=OrganizationCreate(
            organization_name="Acme Manufacturing",
            primary_admin_first_name="Priya",
            primary_admin_last_name="Patel",
            primary_admin_email="priya@acme-manufacturing.example",
        ),
        created_by_user_id=admin.user_id,
    )
    assert pending_user.status == "pending"

    # Cannot log in with the temporary password directly — must activate first.
    login_before_activation = client.post(
        "/api/v1/auth/login", data={"username": pending_user.email, "password": temporary_password}
    )
    assert login_before_activation.status_code == 401

    activate = client.post(
        "/api/v1/auth/activate",
        json={
            "email": pending_user.email,
            "temporary_password": temporary_password,
            "new_password": "a-much-stronger-chosen-password",
        },
    )
    assert activate.status_code == 200
    token = activate.json()["access_token"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == pending_user.email
    assert body["organization_id"] == str(organization.organization_id)

    # And a normal login now works with the chosen password.
    login_after = client.post(
        "/api/v1/auth/login",
        data={"username": pending_user.email, "password": "a-much-stronger-chosen-password"},
    )
    assert login_after.status_code == 200
