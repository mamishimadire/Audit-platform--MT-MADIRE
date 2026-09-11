import uuid
from datetime import datetime
from typing import Literal

from app.schemas.common import OrmModel

ConnectorType = Literal["hubspot"]
AuthorizationStatus = Literal["pending", "authorized", "expired", "revoked"]


class OAuthAuthorizeUrlOut(OrmModel):
    authorize_url: str
    connection_id: uuid.UUID


class OAuthConnectionOut(OrmModel):
    """Never includes the tokens themselves — same write-only-credential
    principle as DataConnectionOut never echoing back encrypted_password."""

    oauth_connection_id: uuid.UUID
    connection_id: uuid.UUID
    organization_id: uuid.UUID
    connector_type: str
    expires_at: datetime | None
    scope: str | None
    authorization_status: AuthorizationStatus
    # Surfaces the exact banner the product spec calls for the moment a
    # client revokes access on HubSpot's side — computed, not stored, so it
    # can never drift from authorization_status.
    needs_reauthorization: bool
