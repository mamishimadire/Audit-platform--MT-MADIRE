"""
The REST / SOAP connection: every configured endpoint is a table of the records it returns.

What is sent, and to whom, is decided by validated settings (see rest_settings), and every request goes
through net_guard.safe_request: the address the name resolves to must be public, the connection is made to
that vetted address, redirects are refused, and the response is size- and time-capped. On top of that:

  * credentials are added here, at request time, from the encrypted secrets; they are never in the
    settings, a response, or an error message
  * a "next page" address that the API supplies must stay on the base URL's own server, so an API (or
    whoever controls a response) cannot make the platform send its credentials somewhere else
  * XML is parsed only with defusedxml: DTDs and entities (XXE, "billion laughs") are refused outright
  * an error from the API is reduced to its HTTP status; its body (which can echo the request) is never shown
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import uuid
from collections import OrderedDict
from urllib.parse import urlencode, urlsplit
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from app.core.net_guard import UnsafeDestination, safe_request
from app.models.data_source import DataConnection
from app.schemas.data_source import DiscoveredEntity, DiscoveredField
from app.services.connectors import ConnectorError, EntityNotHere, register
from app.services.connectors.config import unpack_secrets
from app.services.connectors.file_parsing import table_from_records
from app.services.connectors.rest_settings import PLACEHOLDER

DISCOVERY_RECORDS = 200
# All the pages of one endpoint together. The platform's scheduler loop is waiting on the read, so an API that answers
# slowly, page after page, must not hold it for the sum of every page's own timeout.
READ_BUDGET_SECONDS = 180
DISCOVERY_PAGES = 3
TEST_ENDPOINTS = 5
_CACHE_TTL_SECONDS = 60
_CACHE_MAX = 6
_MAX_XML_DEPTH = 60
_MAX_FLATTEN_DEPTH = 3
_MAX_KEYS_PER_RECORD = 500
_TOKEN_EARLY_EXPIRY_SECONDS = 30
_DEFAULT_TOKEN_LIFETIME_SECONDS = 300

_TOKENS: "dict[uuid.UUID, tuple[str, float, tuple]]" = {}
_RECORDS: "OrderedDict[tuple, tuple[float, int, bool, list[dict]]]" = OrderedDict()
_lock = threading.Lock()


# --- turning a response into records --------------------------------------------------------------------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_obj(element, depth: int):
    if depth > _MAX_XML_DEPTH:
        raise ConnectorError("The XML response is nested too deeply.")
    attrs = {f"@{_local(k)}": v for k, v in element.attrib.items()}
    children = list(element)
    text = (element.text or "").strip()
    if not children and not attrs:
        return text or None
    obj: dict = dict(attrs)
    groups: dict[str, list] = {}
    for child in children:
        groups.setdefault(_local(child.tag), []).append(_xml_obj(child, depth + 1))
    for name, items in groups.items():
        obj[name] = items[0] if len(items) == 1 else items
    if text:
        obj["#text"] = text
    return obj


def _parse_xml(body: bytes) -> dict:
    try:
        root = SafeET.fromstring(body, forbid_dtd=True)
    except DefusedXmlException as exc:
        raise ConnectorError("The response uses XML features (DTDs or entities) that are not accepted.") from exc
    except ParseError as exc:
        raise ConnectorError("The response is not valid XML.") from exc
    return {_local(root.tag): _xml_obj(root, 0)}


def _parse_json(body: bytes):
    try:
        return json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise ConnectorError("The response is not valid JSON.") from exc


def _looks_like_xml(headers: dict, body: bytes) -> bool:
    return "xml" in headers.get("content-type", "").lower() or body.lstrip()[:1] == b"<"


def dig(node, path: str | None):
    """A dotted path into parsed JSON / XML (lists by number). A key that itself contains dots
    (OData's "@odata.nextLink") is found by trying the longest key first. None when it is not there."""
    if not path:
        return node
    return _dig(node, path.split("."))


def _dig(node, steps: list[str]):
    if not steps:
        return node
    if isinstance(node, dict):
        for size in range(len(steps), 0, -1):
            key = ".".join(steps[:size])
            if key in node:
                found = _dig(node[key], steps[size:])
                if found is not None:
                    return found
        return None
    if isinstance(node, list) and steps[0].isdigit() and int(steps[0]) < len(node):
        return _dig(node[int(steps[0])], steps[1:])
    return None


def _flatten(record: dict, prefix: str = "", depth: int = 0, out: dict | None = None) -> dict:
    out = {} if out is None else out
    for key, value in record.items():
        name = f"{prefix}{key}"
        if len(out) >= _MAX_KEYS_PER_RECORD or len(name) > 150:
            continue
        if isinstance(value, dict) and depth < _MAX_FLATTEN_DEPTH and value:
            _flatten(value, f"{name}.", depth + 1, out)
        elif isinstance(value, (dict, list)):
            out[name] = json.dumps(value, default=str)[:2000]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[name] = value
        else:
            out[name] = str(value)
    return out


def _http_message(status: int) -> str:
    if status in (401, 403):
        return f"The API rejected the credentials (HTTP {status})."
    if status == 404:
        return "The API endpoint was not found (HTTP 404) — check the base URL and the path."
    if status == 429:
        return "The API is limiting requests (HTTP 429). Try again later."
    if 400 <= status < 500:
        return f"The API rejected the request (HTTP {status}) — check the endpoint's path and settings."
    return f"The API reported an error (HTTP {status})."


# --- one connection's calls ------------------------------------------------------------------------------
class _Api:
    def __init__(self, connection: DataConnection):
        self.connection_id = connection.connection_id
        self.config = connection.connector_config or {}
        self.secrets = unpack_secrets(connection.encrypted_password)
        self.soap = connection.db_type == "soap_api"
        self.base = self.config.get("base_url", "")
        origin = urlsplit(self.base)
        self.origin = (origin.scheme, (origin.hostname or "").lower(), origin.port or 443)
        self.auth = self.config.get("auth") or {"type": "none"}

    def endpoints(self) -> list[dict]:
        return self.config.get("endpoints") or []

    def endpoint(self, entity_name: str) -> dict | None:
        return next((e for e in self.endpoints() if e["entity_name"] == entity_name), None)

    def signature(self) -> tuple:
        return (self.base, json.dumps(self.config.get("endpoints"), sort_keys=True, default=str), json.dumps(self.auth, sort_keys=True))

    # -- secrets in settings
    def _fill(self, node):
        if isinstance(node, str):
            def replace(match):
                name = match.group(1)
                if name not in self.secrets:
                    raise ConnectorError(f"A setting refers to the secret '{name}', which is not stored on this connection.")
                return self.secrets[name]

            return PLACEHOLDER.sub(replace, node)
        if isinstance(node, dict):
            return {k: self._fill(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self._fill(v) for v in node]
        return node

    # -- authentication
    def _token(self, *, force: bool = False) -> str:
        signature = (self.auth.get("type"), self.auth.get("token_url"), hashlib.sha256(json.dumps(self.secrets, sort_keys=True).encode()).hexdigest())
        with _lock:
            cached = _TOKENS.get(self.connection_id)
        if cached and not force and cached[2] == signature and time.monotonic() < cached[1]:
            return cached[0]
        if self.auth["type"] == "oauth2_client_credentials":
            form = {"grant_type": "client_credentials", "client_id": self.secrets["client_id"], "client_secret": self.secrets["client_secret"]}
        else:
            form = {
                "grant_type": "refresh_token", "refresh_token": self.secrets["refresh_token"],
                "client_id": self.secrets["client_id"], "client_secret": self.secrets["client_secret"],
            }
        if self.auth.get("scope"):
            form["scope"] = self.auth["scope"]
        try:
            response = safe_request(
                "POST", self.auth["token_url"], content=urlencode(form).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            )
        except UnsafeDestination as exc:
            raise ConnectorError(f"The token service could not be reached: {exc}") from exc
        if response.status_code != 200:
            raise ConnectorError(f"The token service refused the credentials (HTTP {response.status_code}).")
        try:
            payload = json.loads(response.body)
            token = payload["access_token"]
            lifetime = float(payload.get("expires_in", _DEFAULT_TOKEN_LIFETIME_SECONDS))
        except (ValueError, KeyError, TypeError) as exc:
            raise ConnectorError("The token service did not return an access token.") from exc
        with _lock:
            _TOKENS[self.connection_id] = (token, time.monotonic() + max(lifetime - _TOKEN_EARLY_EXPIRY_SECONDS, 1), signature)
        return token

    def _apply_auth(self, headers: dict, params: dict, *, refresh: bool) -> None:
        kind = self.auth.get("type", "none")
        if kind == "api_key":
            (headers if self.auth["in"] == "header" else params)[self.auth["name"]] = self.secrets["api_key"]
        elif kind == "bearer":
            headers["Authorization"] = f"{self.auth.get('scheme', 'Bearer')} {self.secrets['token']}"
        elif kind == "basic":
            headers["Authorization"] = "Basic " + base64.b64encode(f"{self.auth['username']}:{self.secrets['password']}".encode()).decode()
        elif kind.startswith("oauth2"):
            headers["Authorization"] = f"{self.auth.get('scheme', 'Bearer')} {self._token(force=refresh)}"

    # -- requests
    def _same_origin(self, url: str) -> str:
        """A next-page address the API gave us: only ever on the base URL's own server."""
        if url.startswith("/") and not url.startswith("//"):
            return self.base_origin_url() + url
        parts = urlsplit(url)
        if (parts.scheme, (parts.hostname or "").lower(), parts.port or 443) != self.origin or parts.username or parts.password:
            raise ConnectorError("The API pointed to a different server for the next page; that is not followed.")
        return url

    def base_origin_url(self) -> str:
        scheme, host, port = self.origin
        return f"{scheme}://{host}" + ("" if port == 443 else f":{port}")

    def request(self, endpoint: dict, *, extra_params: dict | None = None, url: str | None = None):
        params = {**self._fill(endpoint.get("query", {})), **(extra_params or {})}
        headers = {"Accept": "text/xml, application/soap+xml" if self.soap else "application/json"}
        headers.update(self._fill(self.config.get("headers", {})))
        content = json_body = None
        body = endpoint.get("body")
        if self.soap:
            headers["Content-Type"] = "text/xml; charset=utf-8"
            headers["SOAPAction"] = f'"{endpoint.get("soap_action", "")}"'
            content = self._fill(body).encode()
        elif isinstance(body, (dict, list)):
            json_body = self._fill(body)
        elif isinstance(body, str):
            headers.setdefault("Content-Type", "application/json")
            content = self._fill(body).encode()
        target = url or (self.base + endpoint["path"])
        for attempt in (0, 1):
            call_headers, auth_params = dict(headers), {}
            self._apply_auth(call_headers, auth_params, refresh=attempt == 1)
            # A next-page address the API gave us already carries its own query; only authentication is added to it.
            call_params = auth_params if url is not None else {**params, **auth_params}
            try:
                response = safe_request(
                    endpoint["method"], target, headers=call_headers, params=call_params or None, content=content, json_body=json_body,
                )
            except UnsafeDestination as exc:
                raise ConnectorError(str(exc)) from exc
            if response.status_code == 401 and attempt == 0 and self.auth.get("type", "").startswith("oauth2"):
                continue  # the cached token may simply have been revoked: get a fresh one once
            break
        if not 200 <= response.status_code < 300:
            if self.soap and _looks_like_xml(response.headers, response.body):
                self._raise_fault(_parse_xml(response.body))  # a SOAP fault is usually an HTTP 500 with the reason in the body
            raise ConnectorError(_http_message(response.status_code))
        return response

    def _raise_fault(self, doc: dict) -> None:
        fault = dig(doc, "Envelope.Body.Fault")
        if fault is None:
            return
        text = fault.get("faultstring") if isinstance(fault, dict) else None
        if text is None and isinstance(fault, dict):
            reason = dig(fault, "Reason.Text")
            text = reason.get("#text") if isinstance(reason, dict) else reason
        message = "".join(ch for ch in str(text or "the service returned a fault")[:200] if ch.isprintable())
        raise ConnectorError(f"The service returned a SOAP fault: {message}")

    def parse(self, response):
        if response.status_code == 204 or not response.body.strip():
            return None  # "no content": an empty page (some APIs answer 204 once the records run out)
        if self.soap or _looks_like_xml(response.headers, response.body):
            doc = _parse_xml(response.body)
            if self.soap:
                self._raise_fault(doc)
            return doc
        return _parse_json(response.body)

    def extract(self, doc, endpoint: dict) -> list[dict]:
        node = dig(doc, endpoint.get("records_path"))
        if node is None:
            return []
        if isinstance(node, dict):
            node = [node]
        if not isinstance(node, list):
            raise ConnectorError(f"The records path of '{endpoint['entity_name']}' does not point at a list of records.")
        return [_flatten(item) if isinstance(item, dict) else {"value": item} for item in node]

    # -- pages
    def _page_params(self, pagination: dict, state: dict) -> dict:
        kind = pagination["type"]
        params: dict = {}
        if kind == "page":
            params[pagination["page_param"]] = state["page"]
        elif kind == "offset":
            params[pagination["offset_param"]] = state["offset"]
            params[pagination["limit_param"]] = pagination["page_size"]
        elif kind == "cursor" and state["cursor"] is not None:
            params[pagination["cursor_param"]] = state["cursor"]
        if kind in ("page", "cursor") and pagination.get("size_param"):
            params[pagination["size_param"]] = pagination["page_size"]
        return params

    def records(self, endpoint: dict, *, want: int, max_pages: int | None = None) -> tuple[list[dict], bool]:
        """(flattened records, more_may_exist): up to `want` of them, following the endpoint's pagination
        for at most its own page limit (or `max_pages`, if smaller)."""
        pagination = endpoint.get("pagination") or {"type": "none"}
        kind = pagination["type"]
        pages = 1 if kind == "none" else min(pagination.get("max_pages", 1), max_pages or pagination.get("max_pages", 1))
        state = {"page": pagination.get("start", 1), "offset": 0, "cursor": None, "url": None}
        out: list[dict] = []
        previous_page = None
        more = False
        deadline = time.monotonic() + READ_BUDGET_SECONDS
        for _ in range(pages):
            if time.monotonic() > deadline:
                raise ConnectorError("The API is too slow to read within the time allowed.")
            response = self.request(endpoint, extra_params=self._page_params(pagination, state) if state["url"] is None else None, url=state["url"])
            doc = self.parse(response)
            page = self.extract(doc, endpoint)
            fingerprint = hashlib.sha256(json.dumps(page, default=str, sort_keys=True).encode()).hexdigest() if page else None
            if fingerprint is not None and fingerprint == previous_page:
                break  # the server answered with the same page again: it does not honour the paging settings
            previous_page = fingerprint
            out.extend(page)
            if len(out) >= want:
                return out[:want], True
            if kind == "none" or not page:
                return out, False
            more = True
            if kind == "page":
                state["page"] += 1
            elif kind == "offset":
                state["offset"] += pagination["page_size"]
            elif kind == "cursor":
                nxt = dig(doc, pagination["next_cursor_path"])
                if nxt in (None, ""):
                    return out, False
                state["cursor"] = str(nxt)
            elif kind == "next_url":
                nxt = dig(doc, pagination["next_url_path"])
                if not nxt or not isinstance(nxt, str):
                    return out, False
                state["url"] = self._same_origin(nxt)
            elif kind == "link_header":
                nxt = _next_link(response.headers.get("link", ""))
                if nxt is None:
                    return out, False
                state["url"] = self._same_origin(nxt)
        return out, more


def _next_link(header: str) -> str | None:
    for part in header.split(","):
        pieces = part.split(";")
        if len(pieces) < 2:
            continue
        target = pieces[0].strip()
        if target.startswith("<") and target.endswith(">") and any(p.strip().replace(" ", "") in ('rel="next"', "rel=next") for p in pieces[1:]):
            return target[1:-1]
    return None


# --- the connector ---------------------------------------------------------------------------------------
def _cached_records(api: _Api, endpoint: dict, want: int) -> tuple[list[dict], bool]:
    key = (api.connection_id, endpoint["entity_name"], api.signature())
    with _lock:
        hit = _RECORDS.get(key)
    if hit is not None and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS and (hit[1] >= want or not hit[2]):
        return hit[3][:want], hit[2] or len(hit[3]) > want
    records, more = api.records(endpoint, want=want)
    with _lock:
        _RECORDS[key] = (time.monotonic(), want, more, records)
        _RECORDS.move_to_end(key)
        while len(_RECORDS) > _CACHE_MAX:
            _RECORDS.popitem(last=False)
    return records, more


def forget(connection_id: uuid.UUID) -> None:
    with _lock:
        _TOKENS.pop(connection_id, None)
        for key in [k for k in _RECORDS if k[0] == connection_id]:
            _RECORDS.pop(key, None)


class RestConnector:
    def test(self, connection: DataConnection) -> tuple[bool, str]:
        api = _Api(connection)
        answered, failures = 0, []
        for endpoint in api.endpoints()[:TEST_ENDPOINTS]:
            try:
                api.records(endpoint, want=5, max_pages=1)
                answered += 1
            except ConnectorError as exc:
                failures.append(f"'{endpoint['entity_name']}': {exc}")
        if failures:
            return False, "; ".join(failures[:3])
        return True, f"Connected. {answered} endpoint(s) answered."

    def discover(self, connection: DataConnection) -> list[DiscoveredEntity]:
        api = _Api(connection)
        entities = []
        for endpoint in api.endpoints():
            records, more = api.records(endpoint, want=DISCOVERY_RECORDS, max_pages=DISCOVERY_PAGES)
            table = table_from_records(endpoint["entity_name"], records)
            note = " More records exist than were sampled." if more else ""
            entities.append(
                DiscoveredEntity(
                    entity_name=endpoint["entity_name"], entity_type="api",
                    description=f"{endpoint['method']} {endpoint['path']} — {len(records)} record(s) sampled.{note}",
                    fields=[DiscoveredField(field_name=h, data_type=table.types.get(h, "string")) for h in table.headers],
                )
            )
        return entities

    def fetch_records(self, connection: DataConnection, *, entity_name: str, field_names: list[str], limit: int) -> list[dict]:
        api = _Api(connection)
        endpoint = api.endpoint(entity_name)
        if endpoint is None:
            raise EntityNotHere(f"The table '{entity_name}' is not one of this API connection's endpoints.")
        records, _more = _cached_records(api, endpoint, limit)
        table = table_from_records(entity_name, records)
        return [{f: row.get(f) for f in field_names if f in row} for row in table.rows[:limit]]


register("rest_api", RestConnector())
register("soap_api", RestConnector())
