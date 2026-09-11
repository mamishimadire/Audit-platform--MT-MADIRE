import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.core.config import get_settings
from app.db.session import get_db
from app.models.rbac import User
from app.schemas.data_source import DiscoveryPayload
from app.schemas.oauth_connection import OAuthAuthorizeUrlOut, OAuthConnectionOut
from app.services import oauth_connection_service
from app.services.api_connectors import hubspot_connector
from app.services.data_source_service import replace_discovery

router = APIRouter(tags=["hubspot"])


@router.post(
    "/organizations/{organization_id}/data-sources/{data_source_id}/hubspot/connect",
    response_model=OAuthAuthorizeUrlOut,
)
def start_hubspot_connection(
    organization_id: uuid.UUID,
    data_source_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("data_sources:manage")),
) -> OAuthAuthorizeUrlOut:
    enforce_same_organization(organization_id, user, db)
    try:
        oauth_connection, authorize_url = oauth_connection_service.start_hubspot_authorization(
            db, data_source_id=data_source_id, organization_id=organization_id, created_by_user_id=user.user_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return OAuthAuthorizeUrlOut(authorize_url=authorize_url, connection_id=oauth_connection.connection_id)


@router.get("/oauth/hubspot/callback")
def hubspot_callback(code: str, state: str, db: Session = Depends(get_db)):
    """
    Public by necessity — this is where HubSpot redirects the CLIENT'S OWN
    BROWSER after they approve access, not a call this platform's frontend
    ever makes with a session token attached. Security comes entirely from
    `state`: an unguessable, single-use token minted at connect time and
    checked by exact match (see handle_hubspot_callback), the standard
    OAuth2 CSRF defense for exactly this reason.
    """
    settings = get_settings()
    try:
        oauth_connection_service.handle_hubspot_callback(db, code=code, state=state)
        return RedirectResponse(url=f"{settings.frontend_base_url}/data-sources?hubspot=connected")
    except ValueError:
        return RedirectResponse(url=f"{settings.frontend_base_url}/data-sources?hubspot=failed")


@router.post(
    "/organizations/{organization_id}/data-sources/{data_source_id}/hubspot/discover",
    response_model=OAuthConnectionOut,
)
def discover_hubspot_schema(
    organization_id: uuid.UUID,
    data_source_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("data_sources:manage")),
) -> OAuthConnectionOut:
    enforce_same_organization(organization_id, user, db)
    oauth_connection = oauth_connection_service.get_oauth_connection_for_data_source(db, data_source_id=data_source_id)
    if oauth_connection is None or oauth_connection.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No HubSpot connection found for this data source")

    try:
        access_token = oauth_connection_service.get_valid_access_token(db, oauth_connection=oauth_connection)
        entities = hubspot_connector.discover_objects(access_token=access_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    replace_discovery(db, data_source_id=data_source_id, payload=DiscoveryPayload(entities=entities))
    return oauth_connection_service.to_out(oauth_connection)


@router.get(
    "/organizations/{organization_id}/data-sources/{data_source_id}/hubspot/status",
    response_model=OAuthConnectionOut,
)
def get_hubspot_connection_status(
    organization_id: uuid.UUID,
    data_source_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OAuthConnectionOut:
    enforce_same_organization(organization_id, user, db)
    oauth_connection = oauth_connection_service.get_oauth_connection_for_data_source(db, data_source_id=data_source_id)
    if oauth_connection is None or oauth_connection.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No HubSpot connection found for this data source")
    return oauth_connection_service.to_out(oauth_connection)
