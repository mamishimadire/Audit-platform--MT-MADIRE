"""
Validation of REST and SOAP connection settings (registered with config.prepare).

A connection is a base URL, one authentication method, and a list of ENDPOINTS, each of which becomes a
table. Everything the user typed is checked against an allow-list, because what is accepted here is later
used to make requests from the platform's own network position:

  * the base URL is https and its host must not be a private / internal literal address; every request
    then goes through net_guard, which vets the address the name resolves to at the moment of the call
  * an endpoint path is RELATIVE to the base URL (starts with a single "/"): it can never name another host
  * credentials are never stored in the settings, which the API returns. They live in `secrets`, and a
    header, query parameter or body may refer to one only as {{secret.name}}. A key that looks like a
    credential holding a literal value is refused, so a pasted API key cannot end up in a response
  * the framing headers (Host, Content-Length, Transfer-Encoding, ...) belong to the HTTP client, not the user
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.services.connectors import config as base
from app.services.connectors.config import ConfigError, Prepared, _only_keys, _text, check_public_host

MAX_ENDPOINTS = 20
MAX_PAGES_LIMIT = 50
DEFAULT_MAX_PAGES = 10
MAX_PAGE_SIZE = 1000
MAX_SECRETS = 12

PLACEHOLDER = re.compile(r"\{\{secret\.([a-z][a-z0-9_]{0,40})\}\}")
_SECRET_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,59}$")
_PARAM_NAME = re.compile(r"^[A-Za-z0-9_.\-\[\]]{1,60}$")
_PATH_STEP = re.compile(r"^[A-Za-z0-9_@#:\-]+$")
# Keys that, holding a literal value, would be a credential sitting in settings that are returned to the client.
_SENSITIVE_KEY = re.compile(r"(authorization|x-auth|token|secret|passw|api[-_]?key|apikey|bearer|credential|cookie|signature)", re.I)
_FORBIDDEN_HEADERS = {
    "host", "content-length", "transfer-encoding", "connection", "upgrade", "te", "expect", "keep-alive",
    "proxy-authorization", "proxy-connection", "trailer", "content-encoding",
}

# auth type -> (non-secret settings it takes, the secrets it needs)
_AUTH = {
    "none": (set(), set()),
    "api_key": ({"in", "name"}, {"api_key"}),
    "bearer": ({"scheme"}, {"token"}),
    "basic": ({"username"}, {"password"}),
    "oauth2_client_credentials": ({"token_url", "scope", "scheme"}, {"client_id", "client_secret"}),
    "oauth2_refresh_token": ({"token_url", "scope", "scheme"}, {"client_id", "client_secret", "refresh_token"}),
}


def _https_url(value, *, label: str, allow_path: bool) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} is required.")
    value = value.strip()
    if len(value) > 500 or any(c in value for c in "\x00\r\n\\ "):
        raise ConfigError(f"{label} is not a valid address.")
    parts = urlsplit(value)
    if parts.scheme != "https":
        raise ConfigError(f"{label} must start with https://.")
    if not parts.hostname:
        raise ConfigError(f"{label} has no host.")
    if parts.username or parts.password:
        raise ConfigError(f"{label} must not contain a user name or password; use the secrets instead.")
    if parts.fragment or (parts.query and not allow_path):
        raise ConfigError(f"{label} must not contain a fragment{'' if allow_path else ' or query'}.")
    try:
        parts.port  # noqa: B018 — raises ValueError for a malformed port
    except ValueError:
        raise ConfigError(f"{label} has an invalid port.") from None
    check_public_host(parts.hostname)
    return value.rstrip("/") if allow_path else value


def _scalar_map(where: str, given, *, name_pattern: re.Pattern, max_items: int = 20) -> dict:
    if given is None:
        return {}
    if not isinstance(given, dict) or len(given) > max_items:
        raise ConfigError(f"{where} must be a list of name/value pairs (at most {max_items}).")
    cleaned = {}
    for key, value in given.items():
        if not isinstance(key, str) or not name_pattern.match(key):
            raise ConfigError(f"{where}: '{str(key)[:40]}' is not a valid name.")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ConfigError(f"{where}: the value of '{key}' must be text or a number.")
        if isinstance(value, str) and (len(value) > 500 or "\r" in value or "\n" in value or "\x00" in value):
            raise ConfigError(f"{where}: the value of '{key}' is not valid.")
        cleaned[key] = value
    return cleaned


_AUTH_SCHEME = re.compile(r"^[A-Za-z][A-Za-z\-]{0,30}$")
_SCHEME_LABEL = re.compile(r"^[A-Za-z][A-Za-z \-:]{0,30}$")


def _is_placeholder_value(value: str) -> bool:
    """A secret reference, optionally with a short word-like scheme label around it ("Bearer {{secret.token}}",
    "Zoho-oauthtoken {{secret.token}}"). Anything with digits or symbols left over is a literal credential."""
    if not PLACEHOLDER.search(value):
        return False
    rest = PLACEHOLDER.sub("", value).strip()
    return rest == "" or bool(_SCHEME_LABEL.match(rest))


def _refuse_inline_credentials(where: str, node) -> None:
    """Walks a header set, query or JSON body: a credential-looking key with a literal value is refused."""
    if isinstance(node, dict):
        for key, value in node.items():
            if _SENSITIVE_KEY.search(str(key)) and isinstance(value, (str, int, float)) and str(value) != "" and not (
                isinstance(value, str) and _is_placeholder_value(value)
            ):
                raise ConfigError(
                    f"{where}: '{key}' looks like a credential. Put the value in the secrets and refer to it as {{{{secret.name}}}}."
                )
            _refuse_inline_credentials(where, value)
    elif isinstance(node, list):
        for item in node:
            _refuse_inline_credentials(where, item)


# A credential typed into an XML envelope or a JSON/text body, where there is no "key" to inspect: <Password>abc</Password>,
# "client_secret": "abc", password=abc. The value must be a {{secret.name}} reference (or empty).
_TEXT_CREDENTIAL = re.compile(
    r"(?:<\s*(?P<tag>[\w:.\-]*(?:password|passwd|secret|token|apikey|api[-_]?key|credential)[\w:.\-]*)[^>]*>(?P<xml>[^<]*)(?=<)"
    r"|(?P<key>[\w.\-]*(?:password|passwd|secret|token|apikey|api[-_]?key|credential)[\w.\-]*)[\"']?\s*[:=]\s*[\"']?(?P<val>[^\"'\s,;&<]+))",
    re.I,
)


def _refuse_inline_credentials_in_text(where: str, body) -> None:
    if not isinstance(body, str):
        return
    for match in _TEXT_CREDENTIAL.finditer(body):
        name = match.group("tag") or match.group("key")
        value = (match.group("xml") if match.group("tag") else match.group("val")) or ""
        value = value.strip()
        if value and not _is_placeholder_value(value):
            raise ConfigError(
                f"{where}: '{name}' looks like a credential written into the text. Put the value in the secrets and refer to it as {{{{secret.name}}}}."
            )


def _placeholders_in(node) -> set[str]:
    found: set[str] = set()
    if isinstance(node, str):
        found.update(PLACEHOLDER.findall(node))
    elif isinstance(node, dict):
        for value in node.values():
            found |= _placeholders_in(value)
    elif isinstance(node, list):
        for item in node:
            found |= _placeholders_in(item)
    return found


def _bounded_int(where: str, given: dict, key: str, *, default: int, low: int, high: int) -> int:
    value = given.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f"{where}: {key} must be a whole number from {low} to {high}.")
    return value


def _param(where: str, given: dict, key: str, default: str | None = None, *, required: bool = False) -> str | None:
    value = given.get(key, default)
    if value is None:
        if required:
            raise ConfigError(f"{where}: {key} is required.")
        return None
    if not isinstance(value, str) or not _PARAM_NAME.match(value):
        raise ConfigError(f"{where}: {key} is not a valid parameter name.")
    return value


def _dotted_path(where: str, value, *, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise ConfigError(f"{where} is required.")
        return None
    if not isinstance(value, str) or len(value) > 200 or not all(_PATH_STEP.match(step) for step in value.split(".")):
        raise ConfigError(f"{where} must be a dotted path such as data.items.")
    return value


def _pagination(where: str, raw) -> dict:
    raw = raw or {"type": "none"}
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: pagination must be an object.")
    kind = raw.get("type", "none")
    allowed = {
        "none": set(),
        "page": {"page_param", "start", "size_param", "page_size"},
        "offset": {"offset_param", "limit_param", "page_size"},
        "cursor": {"cursor_param", "next_cursor_path", "size_param", "page_size"},
        "next_url": {"next_url_path"},
        "link_header": set(),
    }
    if kind not in allowed:
        raise ConfigError(f"{where}: pagination type must be one of {', '.join(sorted(allowed))}.")
    _only_keys(f"{where} pagination", raw, {"type", "max_pages"} | allowed[kind])
    if kind == "none":
        return {"type": "none"}
    out: dict = {"type": kind, "max_pages": _bounded_int(where, raw, "max_pages", default=DEFAULT_MAX_PAGES, low=1, high=MAX_PAGES_LIMIT)}
    if kind == "page":
        out.update(
            page_param=_param(where, raw, "page_param", "page"), start=_bounded_int(where, raw, "start", default=1, low=0, high=1),
            size_param=_param(where, raw, "size_param"), page_size=_bounded_int(where, raw, "page_size", default=100, low=1, high=MAX_PAGE_SIZE),
        )
    elif kind == "offset":
        out.update(
            offset_param=_param(where, raw, "offset_param", "offset"), limit_param=_param(where, raw, "limit_param", "limit"),
            page_size=_bounded_int(where, raw, "page_size", default=100, low=1, high=MAX_PAGE_SIZE),
        )
    elif kind == "cursor":
        out.update(
            cursor_param=_param(where, raw, "cursor_param", required=True), next_cursor_path=_dotted_path(f"{where}: next_cursor_path", raw.get("next_cursor_path"), required=True),
            size_param=_param(where, raw, "size_param"), page_size=_bounded_int(where, raw, "page_size", default=100, low=1, high=MAX_PAGE_SIZE),
        )
    elif kind == "next_url":
        out["next_url_path"] = _dotted_path(f"{where}: next_url_path", raw.get("next_url_path"), required=True)
    return {k: v for k, v in out.items() if v is not None}


def _endpoint(raw, *, soap: bool, taken: set[str]) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError("Each endpoint must be an object.")
    _only_keys("Endpoint", raw, {"entity_name", "method", "path", "query", "body", "records_path", "pagination", "soap_action"})
    name = _text(raw, "entity_name", label="Table name", max_len=150) or ""
    if name.lower() in taken:
        raise ConfigError(f"Two endpoints have the table name '{name}'.")
    taken.add(name.lower())
    where = f"Endpoint '{name}'"
    method = str(raw.get("method", "POST" if soap else "GET")).upper()
    if method not in (("POST",) if soap else ("GET", "POST")):
        raise ConfigError(f"{where}: the method must be {'POST' if soap else 'GET or POST'}.")
    path = _text(raw, "path", label=f"{where}: path", max_len=500) or ""
    if not path.startswith("/") or path.startswith("//") or any(c in path for c in "?#\\ ") or "://" in path:
        raise ConfigError(f"{where}: the path must start with a single '/', relative to the base URL, with no query or host (use the query settings).")

    query = _scalar_map(f"{where}: query", raw.get("query"), name_pattern=_PARAM_NAME)
    body = raw.get("body")
    if soap:
        if not isinstance(body, str) or not body.strip() or len(body) > 20_000:
            raise ConfigError(f"{where}: a SOAP endpoint needs the request envelope (XML text, up to 20,000 characters).")
        body = body.strip()
    elif body is not None:
        if method != "POST":
            raise ConfigError(f"{where}: only a POST endpoint has a body.")
        if not isinstance(body, (dict, list, str)) or len(str(body)) > 20_000:
            raise ConfigError(f"{where}: the body must be JSON or text of at most 20,000 characters.")
    _refuse_inline_credentials(where, query)
    _refuse_inline_credentials(where, body)
    _refuse_inline_credentials_in_text(where, body)

    cleaned: dict = {"entity_name": name, "method": method, "path": path}
    if query:
        cleaned["query"] = query
    if body is not None:
        cleaned["body"] = body
    records_path = _dotted_path(f"{where}: records_path", raw.get("records_path"))
    if records_path:
        cleaned["records_path"] = records_path
    cleaned["pagination"] = _pagination(where, raw.get("pagination"))
    if soap:
        action = raw.get("soap_action")
        if action is not None:
            if not isinstance(action, str) or len(action) > 200 or any(c in action for c in "\r\n\x00"):
                raise ConfigError(f"{where}: soap_action is not valid.")
            cleaned["soap_action"] = action
    return cleaned


def _auth(raw) -> dict:
    raw = raw or {"type": "none"}
    if not isinstance(raw, dict):
        raise ConfigError("Authentication must be an object.")
    kind = raw.get("type", "none")
    if kind not in _AUTH:
        raise ConfigError(f"Authentication type must be one of {', '.join(sorted(_AUTH))}.")
    _only_keys(f"Authentication ({kind})", raw, {"type"} | _AUTH[kind][0])
    cleaned: dict = {"type": kind}
    scheme = raw.get("scheme")
    if scheme is not None:
        # The word before the token in the Authorization header. "Bearer" unless the API says otherwise
        # (Zoho's is "Zoho-oauthtoken").
        if not isinstance(scheme, str) or not _AUTH_SCHEME.match(scheme):
            raise ConfigError("The authorization scheme must be a single word such as Bearer.")
        cleaned["scheme"] = scheme
    if kind == "api_key":
        location = raw.get("in", "header")
        if location not in ("header", "query"):
            raise ConfigError("An API key goes 'in' the 'header' or the 'query'.")
        cleaned["in"] = location
        name = raw.get("name")
        pattern = _HEADER_NAME if location == "header" else _PARAM_NAME
        if not isinstance(name, str) or not pattern.match(name) or name.lower() in _FORBIDDEN_HEADERS:
            raise ConfigError("The API key needs a valid header or parameter name.")
        cleaned["name"] = name
    elif kind == "basic":
        cleaned["username"] = _text(raw, "username", label="Username", max_len=150)
    elif kind in ("oauth2_client_credentials", "oauth2_refresh_token"):
        cleaned["token_url"] = _https_url(raw.get("token_url"), label="The token URL", allow_path=True)
        scope = _text(raw, "scope", label="Scope", required=False, max_len=300)
        if scope:
            cleaned["scope"] = scope
    return cleaned


def _prepare_api(config: dict, secrets: dict[str, str], *, soap: bool) -> Prepared:
    _only_keys("API", config, {"base_url", "auth", "headers", "endpoints", "preset"})
    base_url = _https_url(config.get("base_url"), label="The base URL", allow_path=True)
    parts = urlsplit(base_url)
    auth = _auth(config.get("auth"))

    headers = _scalar_map("Headers", config.get("headers"), name_pattern=_HEADER_NAME)
    for name in headers:
        if name.lower() in _FORBIDDEN_HEADERS:
            raise ConfigError(f"The header '{name}' is set by the platform and cannot be given.")
    _refuse_inline_credentials("Headers", headers)

    raw_endpoints = config.get("endpoints")
    if not isinstance(raw_endpoints, list) or not 1 <= len(raw_endpoints) <= MAX_ENDPOINTS:
        raise ConfigError(f"Give between 1 and {MAX_ENDPOINTS} endpoints; each becomes a table.")
    taken: set[str] = set()
    endpoints = [_endpoint(e, soap=soap, taken=taken) for e in raw_endpoints]

    needed = _AUTH[auth["type"]][1]
    if len(secrets) > MAX_SECRETS or any(not _SECRET_NAME.match(k) for k in secrets):
        raise ConfigError("Secret names must be lower-case letters, digits and underscores (at most 12 secrets).")
    missing = sorted(needed - {k for k, v in secrets.items() if v})
    if missing:
        raise ConfigError(f"Missing secret(s) for {auth['type']} authentication: {', '.join(missing)}.")
    for name, value in secrets.items():
        if len(value) > base.MAX_SECRET_CHARS:
            raise ConfigError(f"The secret '{name}' is too long.")
        # A secret is substituted into headers, addresses and bodies: a line break in it would be header injection.
        if any(c in value for c in "\r\n\x00"):
            raise ConfigError(f"The secret '{name}' must not contain a line break or control character.")
    referenced = _placeholders_in(headers) | _placeholders_in(endpoints)
    unknown = sorted(referenced - set(secrets))
    if unknown:
        raise ConfigError(f"A value refers to secret(s) that were not supplied: {', '.join(unknown)}.")

    cleaned: dict = {"base_url": base_url, "auth": auth}
    if headers:
        cleaned["headers"] = headers
    cleaned["endpoints"] = endpoints
    preset = config.get("preset")
    if preset is not None:
        if not isinstance(preset, str) or not re.match(r"^[a-z0-9_]{1,40}$", preset):
            raise ConfigError("The preset name is not valid.")
        cleaned["preset"] = preset
    return Prepared(
        config=cleaned, secrets={k: v for k, v in secrets.items() if v},
        host=(parts.hostname or "").lower(), port=parts.port,
    )


def prepare_rest(config: dict, secrets: dict[str, str]) -> Prepared:
    return _prepare_api(config, secrets, soap=False)


def prepare_soap(config: dict, secrets: dict[str, str]) -> Prepared:
    return _prepare_api(config, secrets, soap=True)


base.register_preparer("rest_api", prepare_rest)
base.register_preparer("soap_api", prepare_soap)
