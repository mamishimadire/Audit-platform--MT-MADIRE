"""
Single-session-per-account enforcement, against real HTTP routes and the
real database (see conftest's `client`/`db`/`test_org` fixtures — deleting
test_org's organization cascades through its users to their user_sessions
rows, so nothing here needs its own cleanup).

Run with: pytest tests/test_auth_sessions.py -v
"""
import uuid

from app.core.security import hash_password
from app.models.rbac import Role, User, UserRole

_PASSWORD = "a-strong-enough-password-123"


def _make_login_ready_user(db, org_id):
    role = db.query(Role).filter_by(role_name="Client IT Admin").one()
    user = User(
        organization_id=org_id, first_name="Session", last_name="Test",
        email=f"session-test-{uuid.uuid4().hex}@test.local", password_hash=hash_password(_PASSWORD), status="active",
    )
    db.add(user)
    db.flush()
    db.add(UserRole(user_id=user.user_id, role_id=role.role_id))
    db.commit()
    db.refresh(user)
    return user


def _login(client, user):
    resp = client.post("/api/v1/auth/login", data={"username": user.email, "password": _PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_logging_in_again_invalidates_the_earlier_token(client, db, test_org):
    user = _make_login_ready_user(db, test_org.organization_id)
    first_token = _login(client, user)
    assert client.get("/api/v1/auth/me", headers=_auth(first_token)).status_code == 200

    second_token = _login(client, user)  # a second device/browser logging in
    assert second_token != first_token

    stale = client.get("/api/v1/auth/me", headers=_auth(first_token))
    assert stale.status_code == 401
    assert "signed in somewhere else" in stale.json()["detail"]

    assert client.get("/api/v1/auth/me", headers=_auth(second_token)).status_code == 200, "the newer login must still work"


def test_refresh_keeps_the_same_session_alive_not_a_new_one(client, db, test_org):
    """Refresh must never behave like a second login — it is the SAME
    session getting a new expiry, or a background tab refreshing itself
    would kick out every OTHER tab of that same, still-legitimate session."""
    user = _make_login_ready_user(db, test_org.organization_id)
    token = _login(client, user)

    refreshed = client.post("/api/v1/auth/refresh", headers=_auth(token))
    assert refreshed.status_code == 200
    new_token = refreshed.json()["access_token"]

    assert client.get("/api/v1/auth/me", headers=_auth(new_token)).status_code == 200
    # The original token's session is still the account's current one (only
    # its expiry moved), so it also still works — refreshing in one tab
    # must not log any other tab of the SAME session out.
    assert client.get("/api/v1/auth/me", headers=_auth(token)).status_code == 200


def test_logout_invalidates_the_token_used_to_log_out(client, db, test_org):
    user = _make_login_ready_user(db, test_org.organization_id)
    token = _login(client, user)
    assert client.post("/api/v1/auth/logout", headers=_auth(token)).status_code == 204
    assert client.get("/api/v1/auth/me", headers=_auth(token)).status_code == 401


def test_a_fresh_login_after_logout_works_normally(client, db, test_org):
    """Logging out must not leave the account permanently unable to sign
    back in (a real risk of the "point current_session_id somewhere no
    token can ever match" approach, if that somewhere were reused wrongly)."""
    user = _make_login_ready_user(db, test_org.organization_id)
    first_token = _login(client, user)
    client.post("/api/v1/auth/logout", headers=_auth(first_token))

    new_token = _login(client, user)
    assert client.get("/api/v1/auth/me", headers=_auth(new_token)).status_code == 200


def test_a_token_from_before_single_session_existed_still_works_until_a_real_login(client, db, test_org):
    """Rollout safety: current_session_id is NULL for every account until
    its next real login — an old-shaped token (no "sid" claim) must keep
    working against that NULL, or deploying this would silently sign out
    every already-logged-in user at once."""
    from app.core.security import create_access_token

    user = _make_login_ready_user(db, test_org.organization_id)
    assert user.current_session_id is None
    legacy_token = create_access_token(user_id=user.user_id, organization_id=user.organization_id)  # no session_id

    assert client.get("/api/v1/auth/me", headers=_auth(legacy_token)).status_code == 200

    # The account's first REAL login after that establishes enforcement —
    # the legacy token (still with no "sid" at all) must stop working then,
    # exactly like any other now-superseded token would.
    _login(client, user)
    assert client.get("/api/v1/auth/me", headers=_auth(legacy_token)).status_code == 401
