import types
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.crypto import decrypt_secret
from app.models.data_source import DataSource, OAuthConnection
from app.schemas.data_source import DiscoveredEntity
from app.services import oauth_connection_service
from app.services.api_connectors import hubspot_connector
from app.services.api_connectors.hubspot_connector import HubSpotAuthError


def _fake_settings(**overrides):
    defaults = dict(hubspot_client_id="client-123", hubspot_client_secret="secret-456", hubspot_redirect_uri="https://mt-audit.example.com/api/v1/oauth/hubspot/callback")
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def test_build_authorize_url_requires_configured_credentials(monkeypatch):
    monkeypatch.setattr(hubspot_connector, "get_settings", lambda: _fake_settings(hubspot_client_id=None))
    with pytest.raises(ValueError, match="not configured"):
        hubspot_connector.build_authorize_url(state="abc")


def test_build_authorize_url_includes_state_and_scopes(monkeypatch):
    monkeypatch.setattr(hubspot_connector, "get_settings", lambda: _fake_settings())
    url = hubspot_connector.build_authorize_url(state="csrf-token-123")
    assert url.startswith("https://app.hubspot.com/oauth/authorize?")
    assert "client_id=client-123" in url
    assert "state=csrf-token-123" in url
    assert "crm.objects.contacts.read" in url


def test_expires_at_from_expires_in_leaves_a_safety_margin():
    before = datetime.now(timezone.utc)
    expires_at = hubspot_connector.expires_at_from_expires_in(1800)
    # 1800s requested, minus a 60s margin -> should land ~1740s out, not 1800s.
    delta = (expires_at - before).total_seconds()
    assert 1700 < delta < 1750


_RealClient = httpx.Client


def _patch_httpx_client(monkeypatch, handler):
    """Replaces httpx.Client with one that always uses a mock transport
    hitting `handler` instead of the network — captures the real class
    first so the replacement doesn't recurse into itself."""
    def fake_client(**kwargs):
        kwargs.pop("transport", None)
        return _RealClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)


def test_exchange_code_for_tokens_returns_the_token_payload(monkeypatch):
    monkeypatch.setattr(hubspot_connector, "get_settings", lambda: _fake_settings())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth/v1/token"
        return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 1800})

    _patch_httpx_client(monkeypatch, handler)
    tokens = hubspot_connector.exchange_code_for_tokens(code="auth-code-1")
    assert tokens["access_token"] == "at-1"
    assert tokens["refresh_token"] == "rt-1"


def test_exchange_code_for_tokens_raises_on_non_200(monkeypatch):
    monkeypatch.setattr(hubspot_connector, "get_settings", lambda: _fake_settings())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "invalid_grant"})

    _patch_httpx_client(monkeypatch, handler)
    with pytest.raises(HubSpotAuthError):
        hubspot_connector.exchange_code_for_tokens(code="bad-code")


def test_discover_objects_produces_discovered_entities_with_api_type(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer at-1"
        return httpx.Response(200, json={"results": [
            {"name": "email", "type": "string"},
            {"name": "hs_object_id", "type": "number"},
        ]})

    _patch_httpx_client(monkeypatch, handler)
    entities = hubspot_connector.discover_objects(access_token="at-1")
    assert len(entities) == 3  # contacts, companies, deals
    assert all(isinstance(e, DiscoveredEntity) for e in entities)
    assert all(e.entity_type == "api" for e in entities)
    contacts = next(e for e in entities if e.entity_name == "contacts")
    field_names = {f.field_name for f in contacts.fields}
    assert field_names == {"email", "hs_object_id"}
    id_field = next(f for f in contacts.fields if f.field_name == "hs_object_id")
    assert id_field.is_primary_key is True


def test_discover_objects_raises_auth_error_on_401(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "expired token"})

    _patch_httpx_client(monkeypatch, handler)
    with pytest.raises(HubSpotAuthError):
        hubspot_connector.discover_objects(access_token="stale-token")


# ---- oauth_connection_service: DB-level lifecycle ----


def _make_oauth_connection(db, test_org, **overrides):
    from app.models.data_source import DataConnection

    source = DataSource(organization_id=test_org.organization_id, source_name="HubSpot test source", source_type="hubspot", environment="cloud")
    db.add(source)
    db.flush()
    connection = DataConnection(data_source_id=source.data_source_id, connection_mode="oauth", connection_status="connected")
    db.add(connection)
    db.flush()
    defaults = dict(
        connection_id=connection.connection_id,
        organization_id=test_org.organization_id,
        connector_type="hubspot",
        authorization_status="authorized",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    defaults.update(overrides)
    oauth_connection = OAuthConnection(**defaults)
    db.add(oauth_connection)
    db.commit()
    db.refresh(oauth_connection)
    return oauth_connection


def test_start_hubspot_authorization_creates_pending_connection(db, test_org, maker_user, monkeypatch):
    monkeypatch.setattr(hubspot_connector, "get_settings", lambda: _fake_settings())
    source = DataSource(organization_id=test_org.organization_id, source_name="HubSpot", source_type="hubspot", environment="cloud")
    db.add(source)
    db.commit()
    db.refresh(source)

    oauth_connection, authorize_url = oauth_connection_service.start_hubspot_authorization(
        db, data_source_id=source.data_source_id, organization_id=test_org.organization_id, created_by_user_id=maker_user.user_id
    )
    assert oauth_connection.authorization_status == "pending"
    assert oauth_connection.oauth_state is not None
    assert "state=" in authorize_url


def test_handle_hubspot_callback_rejects_unknown_state(db):
    with pytest.raises(ValueError, match="Invalid or expired"):
        oauth_connection_service.handle_hubspot_callback(db, code="whatever", state="does-not-exist")


def test_handle_hubspot_callback_completes_authorization(db, test_org, maker_user, monkeypatch):
    monkeypatch.setattr(hubspot_connector, "get_settings", lambda: _fake_settings())
    source = DataSource(organization_id=test_org.organization_id, source_name="HubSpot", source_type="hubspot", environment="cloud")
    db.add(source)
    db.commit()
    db.refresh(source)
    oauth_connection, _url = oauth_connection_service.start_hubspot_authorization(
        db, data_source_id=source.data_source_id, organization_id=test_org.organization_id, created_by_user_id=maker_user.user_id
    )
    state = oauth_connection.oauth_state

    monkeypatch.setattr(
        hubspot_connector, "exchange_code_for_tokens",
        lambda *, code: {"access_token": "real-at", "refresh_token": "real-rt", "expires_in": 1800},
    )
    completed = oauth_connection_service.handle_hubspot_callback(db, code="auth-code", state=state)
    assert completed.authorization_status == "authorized"
    assert completed.oauth_state is None  # single-use, cleared after consumption
    assert decrypt_secret(completed.encrypted_access_token) == "real-at"
    assert decrypt_secret(completed.encrypted_refresh_token) == "real-rt"


def test_get_valid_access_token_returns_current_token_without_refreshing_if_not_near_expiry(db, test_org, monkeypatch):
    from app.core.crypto import encrypt_secret

    oauth_connection = _make_oauth_connection(db, test_org, encrypted_access_token=encrypt_secret("still-good"), encrypted_refresh_token=encrypt_secret("rt"))

    def fail_if_called(**kwargs):
        raise AssertionError("refresh_access_token should not have been called")

    monkeypatch.setattr(hubspot_connector, "refresh_access_token", fail_if_called)
    token = oauth_connection_service.get_valid_access_token(db, oauth_connection=oauth_connection)
    assert token == "still-good"


def test_get_valid_access_token_refreshes_when_close_to_expiry(db, test_org, monkeypatch):
    from app.core.crypto import encrypt_secret

    oauth_connection = _make_oauth_connection(
        db, test_org,
        encrypted_access_token=encrypt_secret("stale"),
        encrypted_refresh_token=encrypt_secret("rt-1"),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    monkeypatch.setattr(hubspot_connector, "refresh_access_token", lambda *, refresh_token: {"access_token": "fresh", "refresh_token": "rt-2", "expires_in": 1800})
    token = oauth_connection_service.get_valid_access_token(db, oauth_connection=oauth_connection)
    assert token == "fresh"
    db.refresh(oauth_connection)
    assert decrypt_secret(oauth_connection.encrypted_refresh_token) == "rt-2"


def test_get_valid_access_token_marks_revoked_when_refresh_fails(db, test_org, monkeypatch):
    from app.core.crypto import encrypt_secret

    oauth_connection = _make_oauth_connection(
        db, test_org,
        encrypted_access_token=encrypt_secret("stale"),
        encrypted_refresh_token=encrypt_secret("rt-1"),
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )

    def raise_auth_error(**kwargs):
        raise HubSpotAuthError("refresh token no longer valid")

    monkeypatch.setattr(hubspot_connector, "refresh_access_token", raise_auth_error)
    with pytest.raises(ValueError, match="Connection lost"):
        oauth_connection_service.get_valid_access_token(db, oauth_connection=oauth_connection)
    db.refresh(oauth_connection)
    assert oauth_connection.authorization_status == "revoked"


def test_get_valid_access_token_rejects_already_revoked_connection(db, test_org):
    oauth_connection = _make_oauth_connection(db, test_org, authorization_status="revoked")
    with pytest.raises(ValueError, match="Connection lost"):
        oauth_connection_service.get_valid_access_token(db, oauth_connection=oauth_connection)


def test_to_out_never_exposes_the_tokens(db, test_org):
    from app.core.crypto import encrypt_secret

    oauth_connection = _make_oauth_connection(db, test_org, encrypted_access_token=encrypt_secret("secret-at"), encrypted_refresh_token=encrypt_secret("secret-rt"))
    out = oauth_connection_service.to_out(oauth_connection)
    dumped = out.model_dump()
    assert "secret-at" not in str(dumped)
    assert "secret-rt" not in str(dumped)
    assert "encrypted_access_token" not in dumped
    assert dumped["needs_reauthorization"] is False


def test_to_out_flags_needs_reauthorization_when_revoked(db, test_org):
    oauth_connection = _make_oauth_connection(db, test_org, authorization_status="revoked")
    out = oauth_connection_service.to_out(oauth_connection)
    assert out.needs_reauthorization is True
