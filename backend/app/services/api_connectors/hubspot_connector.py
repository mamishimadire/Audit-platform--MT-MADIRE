"""
HubSpot OAuth API connector — the proof-of-concept for a connector family
that is NOT a database. Deliberately a plain module of functions, matching
every other connector in this codebase (data_source_service.py,
software_compliance_service.py, ...) rather than a class hierarchy: nothing
here needs polymorphic dispatch across API vendors yet (HubSpot is the
first and only one), and a bare class with one implementation would be
ceremony, not abstraction. When a second API vendor is added, the parts
that turn out to genuinely repeat (token refresh scheduling, discovery ->
DiscoveredEntity conversion) are what should get pulled into a shared
module — not guessed at up front.

Every function here takes plain values (tokens, codes) and returns plain
dicts/schema objects — no database session, no ORM objects. Persistence
and the encrypt/decrypt-at-rest boundary live in oauth_connection_service.py,
one layer up, the same separation data_source_service.py already has
between "build a SQLAlchemy engine" and "store/retrieve the password."
"""
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import get_settings
from app.schemas.data_source import DiscoveredEntity, DiscoveredField

AUTHORIZE_URL = "https://app.hubspot.com/oauth/authorize"
TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
API_BASE_URL = "https://api.hubapi.com"

# The three core CRM objects — enough to prove the model end-to-end.
# Adding a fourth (e.g. tickets) is one more entry here, not a pipeline change.
_DISCOVERABLE_OBJECTS = ("contacts", "companies", "deals")

SCOPES = "crm.objects.contacts.read crm.objects.companies.read crm.objects.deals.read"

_HTTP_TIMEOUT_SECONDS = 15


class HubSpotAuthError(Exception):
    """Raised when HubSpot rejects a token exchange/refresh — e.g. the
    client revoked access, or a refresh token was already used/expired.
    The caller (oauth_connection_service) turns this into
    authorization_status='revoked'."""


def _require_app_credentials() -> tuple[str, str, str]:
    settings = get_settings()
    if not settings.hubspot_client_id or not settings.hubspot_client_secret or not settings.hubspot_redirect_uri:
        raise ValueError(
            "HubSpot is not configured on this platform yet — hubspot_client_id/hubspot_client_secret/"
            "hubspot_redirect_uri must be set before any organization can connect HubSpot."
        )
    return settings.hubspot_client_id, settings.hubspot_client_secret, settings.hubspot_redirect_uri


def build_authorize_url(*, state: str) -> str:
    client_id, _secret, redirect_uri = _require_app_credentials()
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "scope": SCOPES, "state": state}
    query = str(httpx.QueryParams(params))
    return f"{AUTHORIZE_URL}?{query}"


def _token_response_to_dict(response: httpx.Response) -> dict[str, Any]:
    if response.status_code != 200:
        raise HubSpotAuthError(f"HubSpot token endpoint returned {response.status_code}: {response.text[:200]}")
    return response.json()


def exchange_code_for_tokens(*, code: str) -> dict[str, Any]:
    """Returns {access_token, refresh_token, expires_in, ...} straight from
    HubSpot — the caller decides what to persist and how to encrypt it."""
    client_id, client_secret, redirect_uri = _require_app_credentials()
    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        response = client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
    return _token_response_to_dict(response)


def refresh_access_token(*, refresh_token: str) -> dict[str, Any]:
    client_id, client_secret, _redirect_uri = _require_app_credentials()
    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        response = client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )
    return _token_response_to_dict(response)


def expires_at_from_expires_in(expires_in: int) -> datetime:
    # HubSpot access tokens are short-lived (~30 min) by design — a 60s
    # safety margin means a token is never used right as it turns invalid.
    return datetime.now(timezone.utc) + timedelta(seconds=max(expires_in - 60, 0))


def discover_objects(*, access_token: str) -> list[DiscoveredEntity]:
    """One DiscoveredEntity per CRM object (contacts/companies/deals), one
    DiscoveredField per HubSpot property on that object — the exact same
    shape discover_direct_connection_schema produces for a SQL table, so
    this feeds replace_discovery unchanged."""
    entities: list[DiscoveredEntity] = []
    headers = {"Authorization": f"Bearer {access_token}"}
    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS, headers=headers) as client:
        for object_name in _DISCOVERABLE_OBJECTS:
            response = client.get(f"{API_BASE_URL}/crm/v3/properties/{object_name}")
            if response.status_code == 401:
                raise HubSpotAuthError("HubSpot rejected the access token while listing properties")
            response.raise_for_status()
            properties = response.json().get("results", [])
            fields = [
                DiscoveredField(
                    field_name=prop["name"],
                    data_type=prop.get("type", "string"),
                    is_primary_key=(prop["name"] == "hs_object_id"),
                    is_sensitive=False,
                )
                for prop in properties
            ]
            entities.append(DiscoveredEntity(entity_name=object_name, entity_type="api", description=f"HubSpot CRM object: {object_name}", fields=fields))
    return entities


def fetch_object_records(*, access_token: str, object_name: str, property_names: list[str], since: datetime | None = None) -> list[dict[str, Any]]:
    """Paginated fetch of one CRM object's records for a scheduled audit
    test execution — mirrors what a Gateway-executed SQL rule does with a
    result set, just over HTTP instead of a database cursor."""
    if object_name not in _DISCOVERABLE_OBJECTS:
        raise ValueError(f"Unknown HubSpot object '{object_name}' — expected one of {_DISCOVERABLE_OBJECTS}")

    headers = {"Authorization": f"Bearer {access_token}"}
    records: list[dict[str, Any]] = []
    after: str | None = None
    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS, headers=headers) as client:
        while True:
            params: dict[str, Any] = {"properties": ",".join(property_names), "limit": 100}
            if after:
                params["after"] = after
            response = client.get(f"{API_BASE_URL}/crm/v3/objects/{object_name}", params=params)
            if response.status_code == 401:
                raise HubSpotAuthError("HubSpot rejected the access token while fetching records")
            response.raise_for_status()
            body = response.json()
            for item in body.get("results", []):
                if since is not None:
                    updated_at = item.get("updatedAt")
                    if updated_at and datetime.fromisoformat(updated_at.replace("Z", "+00:00")) < since:
                        continue
                records.append(item)
            after = body.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
    return records


def rate_limit_policy() -> dict[str, Any]:
    """Documented HubSpot defaults for a standard app — the execution
    scheduler can use this to avoid hammering a client's HubSpot account,
    same spirit as the Gateway not polling a client database in a tight loop."""
    return {"requests_per_10_seconds": 100, "requests_per_day": 250_000}
