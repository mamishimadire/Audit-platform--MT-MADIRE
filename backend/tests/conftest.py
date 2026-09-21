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


# ---- fixtures for the onboarding / login / tenant-isolation tests (test_auth, test_tenant_isolation) ----------
# These tests drive the REAL onboarding services, which create organizations and users, and they run against
# the same database as the live app (see the note on `db`). They had never run: the two fixtures below did not
# exist. So they are safe to run only because everything they create is removed afterwards, strictly by the
# names and emails they use (never "whatever appeared during the test", which could include a real sign-up),
# and because they refuse to run if a real organization already has one of those names.
import re

_ONBOARDING_TEST_ORG_NAMES = ("Acme Manufacturing", "Org A Pty Ltd", "Org B Pty Ltd", "Solo Org")
_ONBOARDING_TEST_EMAIL = re.compile(
    r"^(admin-[0-9a-f-]{36}@example\.com|priya@acme-manufacturing\.example|admin-(a|b)@org-(a|b)\.example|admin-solo@org-solo\.example)$"
)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    # A real client address: audit_logs.ip_address is an INET column and rejects Starlette's default host "testclient".
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture
def db_session(db):
    from sqlalchemy import text

    taken = db.execute(
        text("select organization_name from organizations where organization_name = any(:names)"), {"names": list(_ONBOARDING_TEST_ORG_NAMES)}
    ).all()
    if taken:
        pytest.skip(f"an organization named {taken[0][0]!r} already exists: refusing to run (and later delete) against it")
    yield db
    db.rollback()
    doomed_users = [
        u for u in db.execute(text("select user_id, email from users")).all() if _ONBOARDING_TEST_EMAIL.match(u.email)
    ]
    for user in doomed_users:
        db.execute(text("delete from users where user_id = :u"), {"u": user.user_id})
    db.execute(text("delete from organizations where organization_name = any(:names)"), {"names": list(_ONBOARDING_TEST_ORG_NAMES)})
    db.commit()


# ---- shared by the file / SFTP / API connection tests ------------------------------------------------------
# Real users with real roles over real HTTP, in one organization plus an unrelated one, so the same
# authorization and tenant-isolation checks apply to every connection family.
def make_user(db, *, role_name: str, org_id):
    user = User(
        organization_id=org_id, first_name="Conn", last_name="Test", email=f"conn-test-{uuid.uuid4().hex}@test.local",
        password_hash="not-a-real-hash", status="active",
    )
    db.add(user)
    db.flush()
    db.add(UserRole(user_id=user.user_id, role_id=db.query(Role).filter(Role.role_name == role_name).one().role_id))
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def connector_world(db, test_org, monkeypatch):
    from types import SimpleNamespace

    from sqlalchemy import text

    from app.api.v1.routes import data_sources as routes
    from app.models.data_source import DataSource

    # No background profiling thread: it would outlive the test and write after teardown. A test that needs
    # profiles calls the profiler itself; tests can also check the thread WAS requested (profiling_requests).
    started = []
    monkeypatch.setattr(routes, "profile_data_source_in_background", lambda source_id: started.append(source_id))
    other = Organization(organization_name=f"Connector Other Org {uuid.uuid4().hex[:8]}", status="active")
    source = DataSource(organization_id=test_org.organization_id, source_name="connector test source", source_type="file", environment="cloud")
    db.add_all([other, source])
    db.commit()
    manager = make_user(db, role_name="Client IT Admin", org_id=test_org.organization_id)
    read_only = make_user(db, role_name="Read Only", org_id=test_org.organization_id)
    outsider = make_user(db, role_name="Client IT Admin", org_id=other.organization_id)
    yield SimpleNamespace(
        org=test_org, other=other, source=source, manager=manager, read_only=read_only, outsider=outsider, profiling_requests=started
    )
    db.rollback()
    for user in (manager, read_only, outsider):
        db.delete(db.get(User, user.user_id))
    db.commit()
    db.delete(db.get(DataSource, source.data_source_id))
    db.commit()
    db.delete(db.get(Organization, other.organization_id))
    db.commit()
    # Cleanup is verified, not assumed: nothing of this test may remain.
    assert db.execute(text("select count(*) from data_connections where data_source_id = :s"), {"s": source.data_source_id}).scalar() == 0


@pytest.fixture
def connector_approver(db, connector_world):
    """A second person with the right to approve connection changes: an edit must never be approved by whoever proposed it."""
    user = make_user(db, role_name="Audit Manager", org_id=connector_world.org.organization_id)
    yield user
    db.rollback()
    db.delete(db.get(User, user.user_id))
    db.commit()
