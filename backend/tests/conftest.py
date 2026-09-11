import uuid

import pytest

from app.db.session import SessionLocal
from app.models.organization import Organization
from app.models.rbac import Role, User, UserOrganizationScope, UserRole


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def test_org(db):
    org = Organization(organization_name=f"SoD Test Org {uuid.uuid4().hex[:8]}", status="active")
    db.add(org)
    db.commit()
    db.refresh(org)
    yield org
    db.delete(org)
    db.commit()


def _make_platform_user(db, *, role_name: str, org_id: uuid.UUID) -> User:
    """A platform user (organization_id IS NULL) scoped to one test org via
    user_organization_scope — the same shape a real Auditor/Audit Manager
    account has, not a shortcut that only works in tests."""
    user = User(
        organization_id=None,
        first_name="SoD",
        last_name="Test",
        email=f"sod-test-{uuid.uuid4().hex}@test.local",
        password_hash="not-a-real-hash",
        status="active",
    )
    db.add(user)
    db.flush()
    role = db.query(Role).filter(Role.role_name == role_name).one()
    db.add(UserRole(user_id=user.user_id, role_id=role.role_id))
    db.add(UserOrganizationScope(user_id=user.user_id, organization_id=org_id))
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def maker_user(db, test_org):
    user = _make_platform_user(db, role_name="Auditor", org_id=test_org.organization_id)
    yield user
    db.delete(user)
    db.commit()


@pytest.fixture
def checker_user(db, test_org):
    user = _make_platform_user(db, role_name="Audit Manager", org_id=test_org.organization_id)
    yield user
    db.delete(user)
    db.commit()
