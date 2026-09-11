"""
Generic OAuth connection lifecycle — the part of an API connector that
does NOT vary by vendor: persisting a token pair encrypted at rest,
deciding when to refresh, and recording what to do when the vendor rejects
a refresh (the client revoked access on their side). HubSpot-specific HTTP
calls live in api_connectors/hubspot_connector.py; this module only calls
those functions and handles the database side, the same separation
data_source_service.py keeps between "build an engine" and "store a
password."
"""
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.data_source import DataConnection, DataSource, OAuthConnection
from app.schemas.oauth_connection import OAuthConnectionOut
from app.services.api_connectors import hubspot_connector
from app.services.api_connectors.hubspot_connector import HubSpotAuthError
from app.services.audit_log_service import log_action

# A refreshed token is fetched this long before it actually expires, so a
# slow discovery/execution run started right before expiry never gets cut
# off mid-flight by a suddenly-invalid token.
_REFRESH_MARGIN_SECONDS = 120


def to_out(entry: OAuthConnection) -> OAuthConnectionOut:
    return OAuthConnectionOut(
        oauth_connection_id=entry.oauth_connection_id,
        connection_id=entry.connection_id,
        organization_id=entry.organization_id,
        connector_type=entry.connector_type,
        expires_at=entry.expires_at,
        scope=entry.scope,
        authorization_status=entry.authorization_status,
        needs_reauthorization=entry.authorization_status == "revoked",
    )


def start_hubspot_authorization(
    db: Session, *, data_source_id: uuid.UUID, organization_id: uuid.UUID, created_by_user_id: uuid.UUID
) -> tuple[OAuthConnection, str]:
    """Creates the DataConnection + OAuthConnection pair in 'pending' state
    and returns the URL to send the user's browser to. Nothing is
    authorized yet — that only happens once HubSpot redirects back to
    handle_hubspot_callback with a real authorization code."""
    data_source = db.get(DataSource, data_source_id)
    if data_source is None or data_source.organization_id != organization_id:
        raise ValueError("Data source not found")

    connection = DataConnection(
        data_source_id=data_source_id,
        connection_mode="oauth",
        connection_status="pending",
        created_by=created_by_user_id,
    )
    db.add(connection)
    db.flush()

    state = secrets.token_urlsafe(32)
    oauth_connection = OAuthConnection(
        connection_id=connection.connection_id,
        organization_id=organization_id,
        connector_type="hubspot",
        authorization_status="pending",
        oauth_state=state,
        created_by=created_by_user_id,
    )
    db.add(oauth_connection)
    log_action(
        db,
        action=f"Started HubSpot authorization for data source '{data_source.source_name}'",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="oauth_connections",
        entity_id=oauth_connection.oauth_connection_id,
    )
    db.commit()
    db.refresh(oauth_connection)

    authorize_url = hubspot_connector.build_authorize_url(state=state)
    return oauth_connection, authorize_url


def handle_hubspot_callback(db: Session, *, code: str, state: str) -> OAuthConnection:
    """Called from the public callback route HubSpot redirects the user's
    browser to. `state` is the only thing tying this request back to a
    specific pending connection — validated by exact match against the
    single-use token stored at start_hubspot_authorization, then cleared so
    it can never be replayed."""
    oauth_connection = db.scalar(select(OAuthConnection).where(OAuthConnection.oauth_state == state))
    if oauth_connection is None:
        raise ValueError("Invalid or expired authorization state — please restart the HubSpot connection from Data Sources.")

    tokens = hubspot_connector.exchange_code_for_tokens(code=code)

    oauth_connection.encrypted_access_token = encrypt_secret(tokens["access_token"])
    oauth_connection.encrypted_refresh_token = encrypt_secret(tokens["refresh_token"])
    oauth_connection.expires_at = hubspot_connector.expires_at_from_expires_in(tokens.get("expires_in", 1800))
    oauth_connection.scope = tokens.get("scope") or hubspot_connector.SCOPES
    oauth_connection.authorization_status = "authorized"
    oauth_connection.oauth_state = None  # single-use — cannot be replayed

    connection = db.get(DataConnection, oauth_connection.connection_id)
    if connection is not None:
        connection.connection_status = "connected"
        connection.last_tested_at = datetime.now(timezone.utc)

    log_action(
        db,
        action="HubSpot authorization completed",
        organization_id=oauth_connection.organization_id,
        entity_type="oauth_connections",
        entity_id=oauth_connection.oauth_connection_id,
        new_value={"authorization_status": "authorized"},
    )
    db.commit()
    db.refresh(oauth_connection)
    return oauth_connection


def _mark_revoked(db: Session, *, oauth_connection: OAuthConnection) -> None:
    oauth_connection.authorization_status = "revoked"
    connection = db.get(DataConnection, oauth_connection.connection_id)
    if connection is not None:
        connection.connection_status = "revoked"
    log_action(
        db,
        action="HubSpot connection lost — reauthorization required",
        organization_id=oauth_connection.organization_id,
        entity_type="oauth_connections",
        entity_id=oauth_connection.oauth_connection_id,
        new_value={"authorization_status": "revoked"},
    )
    db.commit()


def get_valid_access_token(db: Session, *, oauth_connection: OAuthConnection) -> str:
    """The one function every HubSpot-calling code path (discovery,
    scheduled execution) must go through — refreshes transparently when
    needed, and turns a refresh failure into the exact 'reauthorization
    required' state the UI checks for, instead of a raw HTTP error
    surfacing from deep inside a discovery run."""
    if oauth_connection.authorization_status == "revoked":
        raise ValueError("Connection lost — reauthorization required.")
    if not oauth_connection.encrypted_access_token or not oauth_connection.encrypted_refresh_token:
        raise ValueError("This HubSpot connection was never completed — reauthorization required.")

    now = datetime.now(timezone.utc)
    expires_at = oauth_connection.expires_at
    still_valid = expires_at is not None and (expires_at - now).total_seconds() > _REFRESH_MARGIN_SECONDS
    if still_valid:
        return decrypt_secret(oauth_connection.encrypted_access_token)

    try:
        refresh_token = decrypt_secret(oauth_connection.encrypted_refresh_token)
        tokens = hubspot_connector.refresh_access_token(refresh_token=refresh_token)
    except HubSpotAuthError:
        _mark_revoked(db, oauth_connection=oauth_connection)
        raise ValueError("Connection lost — reauthorization required.") from None

    oauth_connection.encrypted_access_token = encrypt_secret(tokens["access_token"])
    # HubSpot may or may not rotate the refresh token on refresh; keep the
    # existing one unless a new one is actually issued.
    if tokens.get("refresh_token"):
        oauth_connection.encrypted_refresh_token = encrypt_secret(tokens["refresh_token"])
    oauth_connection.expires_at = hubspot_connector.expires_at_from_expires_in(tokens.get("expires_in", 1800))
    oauth_connection.authorization_status = "authorized"
    db.commit()
    db.refresh(oauth_connection)
    return decrypt_secret(oauth_connection.encrypted_access_token)


def get_oauth_connection_for_data_source(db: Session, *, data_source_id: uuid.UUID) -> OAuthConnection | None:
    return db.scalar(
        select(OAuthConnection)
        .join(DataConnection, DataConnection.connection_id == OAuthConnection.connection_id)
        .where(DataConnection.data_source_id == data_source_id)
    )
