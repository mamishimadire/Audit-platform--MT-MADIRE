"""
The REST / SOAP connector against a REAL local HTTPS server (throwaway certificate), so TLS with the
host name kept for SNI, request building, authentication, pagination, JSON and XML parsing and every
refusal are exercised for real. The only substitution is the network policy: net_guard is told the
stand-in name is public (the real policy refuses 127.0.0.1, and the tests that check the policy leave it
in force). No database is needed: connections are built the way production builds them (validated by
config.prepare, secrets packed into one encrypted blob) and handed to the connector directly.
"""
import datetime
import json
import socket
import ssl
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.core import net_guard
from app.core.net_guard import is_public_ip as REAL_IS_PUBLIC_IP
from app.services import connectors
from app.services.connectors import ConnectorError, EntityNotHere, config as connector_settings, rest_connector

NAME = "api.example.test"
SECRET_MARKER = "sk-very-secret-marker-7731"


# --- a local HTTPS server --------------------------------------------------------------------------------------
def _self_signed(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NAME)])
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(7)
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(NAME)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    return cert_path, key_path


def json_resp(obj, status=200, headers=None):
    return status, {"Content-Type": "application/json", **(headers or {})}, json.dumps(obj).encode()


def xml_resp(text, status=200, content_type="application/xml"):
    return status, {"Content-Type": content_type}, text.encode()


class FakeApi:
    def __init__(self, port):
        self.port = port
        self.base = f"https://{NAME}:{port}"
        self.routes: dict[tuple[str, str], object] = {}
        self.requests: list[SimpleNamespace] = []

    def route(self, method, path, handler):
        self.routes[(method, path)] = handler

    def hits(self, path):
        return [r for r in self.requests if r.path == path]


@pytest.fixture
def api(tmp_path, monkeypatch):
    cert, key = _self_signed(tmp_path)
    fake = FakeApi(0)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _handle(self):
            parts = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            request = SimpleNamespace(
                method=self.command, path=parts.path, query={k: v[0] for k, v in parse_qs(parts.query).items()},
                headers={k.lower(): v for k, v in self.headers.items()}, body=self.rfile.read(length) if length else b"",
            )
            fake.requests.append(request)
            handler = fake.routes.get((self.command, parts.path))
            status, headers, body = handler(request) if handler else (404, {}, b"not found")
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _handle

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    fake.port = server.server_address[1]
    fake.base = f"https://{NAME}:{fake.port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client_ctx = ssl.create_default_context(cafile=str(cert))
    monkeypatch.setattr(net_guard, "is_public_ip", lambda ip: True)
    monkeypatch.setattr(net_guard, "_resolver", lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))])
    monkeypatch.setattr(net_guard, "_default_verify", lambda: client_ctx)
    rest_connector._RECORDS.clear()
    rest_connector._TOKENS.clear()
    yield fake
    server.shutdown()
    rest_connector._RECORDS.clear()
    rest_connector._TOKENS.clear()


def connection(api, *, endpoints=None, auth=None, secrets=None, headers=None, kind="rest_api", drop_secrets=()):
    """A connection built the way production builds one: validated, secrets in one encrypted blob."""
    config = {"base_url": api.base, "auth": auth or {"type": "none"}, "endpoints": endpoints or [{"entity_name": "invoices", "path": "/invoices"}]}
    if headers:
        config["headers"] = headers
    prepared = connector_settings.prepare(kind, config, secrets or {})
    stored = {k: v for k, v in prepared.secrets.items() if k not in drop_secrets}
    return SimpleNamespace(
        connection_id=uuid.uuid4(), db_type=kind, connector_config=prepared.config, encrypted_password=connector_settings.pack_secrets(stored),
        host=prepared.host, port=prepared.port, username=None,
    )


def fetch(conn, entity="invoices", fields=None, limit=1000):
    return connectors.for_connection(conn).fetch_records(conn, entity_name=entity, field_names=fields or [], limit=limit)


INVOICES = [
    {"id": "A1", "amount": "10.50", "customer": {"name": "Acme", "tier": 2}, "tags": ["x", "y"]},
    {"id": "A2", "amount": "7", "customer": {"name": "Beta", "tier": 3}, "tags": []},
]


# --- discovery and typing --------------------------------------------------------------------------------------
def test_records_are_discovered_flattened_and_typed_and_read_back_typed(api):
    api.route("GET", "/invoices", lambda r: json_resp(INVOICES))
    conn = connection(api)
    [entity] = connectors.for_connection(conn).discover(conn)
    assert entity.entity_name == "invoices" and entity.entity_type == "api"
    types = {f.field_name: f.data_type for f in entity.fields}
    assert types == {"id": "string", "amount": "double", "customer.name": "string", "customer.tier": "integer", "tags": "string"}
    rows = fetch(conn, fields=["id", "amount", "customer.tier"])
    assert rows == [{"id": "A1", "amount": 10.5, "customer.tier": 2}, {"id": "A2", "amount": 7.0, "customer.tier": 3}]  # "10.50" arrives as text, is a number


def test_a_records_path_and_a_single_object_response(api):
    api.route("GET", "/invoices", lambda r: json_resp({"data": {"items": INVOICES}, "total": 2}))
    api.route("GET", "/one", lambda r: json_resp({"data": INVOICES[0]}))
    conn = connection(api, endpoints=[
        {"entity_name": "invoices", "path": "/invoices", "records_path": "data.items"},
        {"entity_name": "one", "path": "/one", "records_path": "data"},
        {"entity_name": "missing", "path": "/one", "records_path": "data.nothing"},
    ])
    assert len(fetch(conn, "invoices", ["id"])) == 2
    assert fetch(conn, "one", ["id"]) == [{"id": "A1"}]  # one object is one record
    assert fetch(conn, "missing", ["id"]) == []  # a path that finds nothing is an empty table, not an error


def test_a_records_path_that_is_not_a_list_of_records_is_explained(api):
    api.route("GET", "/invoices", lambda r: json_resp({"data": "just text"}))
    conn = connection(api, endpoints=[{"entity_name": "invoices", "path": "/invoices", "records_path": "data"}])
    with pytest.raises(ConnectorError, match="records path"):
        fetch(conn)


def test_a_response_that_is_not_json_is_explained(api):
    api.route("GET", "/invoices", lambda r: (200, {"Content-Type": "text/plain"}, b"hello there"))
    with pytest.raises(ConnectorError, match="not valid JSON"):
        fetch(connection(api))


def test_a_table_that_is_not_one_of_the_endpoints_is_rejected_without_a_request(api):
    conn = connection(api)
    with pytest.raises(EntityNotHere):
        fetch(conn, "customers")
    assert api.requests == []


# --- pagination ------------------------------------------------------------------------------------------------
def _numbered(n):
    return [{"n": i} for i in range(n)]


def test_page_number_pagination_reads_every_page_and_stops_when_enough_is_read(api):
    data = _numbered(7)

    def handler(r):
        page, size = int(r.query.get("page", 1)), int(r.query.get("per_page", 3))
        return json_resp(data[(page - 1) * size : page * size])

    api.route("GET", "/items", handler)
    conn = connection(api, endpoints=[{"entity_name": "items", "path": "/items", "pagination": {"type": "page", "size_param": "per_page", "page_size": 3}}])
    assert [r["n"] for r in fetch(conn, "items", ["n"])] == list(range(7))  # pages of 3, 3, 1, then an empty one
    assert [r.query["page"] for r in api.hits("/items")] == ["1", "2", "3", "4"]
    assert all(r.query["per_page"] == "3" for r in api.hits("/items"))

    rest_connector._RECORDS.clear()
    api.requests.clear()
    assert len(fetch(conn, "items", ["n"], limit=4)) == 4
    assert len(api.hits("/items")) == 2  # it did not read pages it did not need


def test_the_page_limit_bounds_how_much_is_read(api):
    api.route("GET", "/items", lambda r: json_resp([{"n": int(r.query.get("page", 1))}]))  # an endless supply
    conn = connection(api, endpoints=[{"entity_name": "items", "path": "/items", "pagination": {"type": "page", "max_pages": 3}}])
    assert len(fetch(conn, "items", ["n"])) == 3 and len(api.hits("/items")) == 3


def test_offset_pagination(api):
    data = _numbered(25)
    api.route("GET", "/items", lambda r: json_resp(data[int(r.query["offset"]) : int(r.query["offset"]) + int(r.query["limit"])]))
    conn = connection(api, endpoints=[{"entity_name": "items", "path": "/items", "pagination": {"type": "offset", "page_size": 10}}])
    assert len(fetch(conn, "items", ["n"])) == 25
    assert [r.query["offset"] for r in api.hits("/items")] == ["0", "10", "20", "30"]


def test_cursor_pagination(api):
    pages = {None: ([{"n": 1}, {"n": 2}], "c2"), "c2": ([{"n": 3}], "c3"), "c3": ([{"n": 4}], None)}
    api.route("GET", "/items", lambda r: json_resp({"items": pages[r.query.get("after")][0], "meta": {"next": pages[r.query.get("after")][1]}}))
    conn = connection(api, endpoints=[{
        "entity_name": "items", "path": "/items", "records_path": "items",
        "pagination": {"type": "cursor", "cursor_param": "after", "next_cursor_path": "meta.next"},
    }])
    assert [r["n"] for r in fetch(conn, "items", ["n"])] == [1, 2, 3, 4] and len(api.hits("/items")) == 3


def test_next_url_and_link_header_pagination_stay_on_the_apis_own_server(api):
    def by_url(r):
        page = int(r.query.get("page", 1))
        nxt = {1: f"{api.base}/items?page=2", 2: "/items?page=3"}.get(page)  # an absolute and a relative address
        return json_resp({"items": [{"n": page}], "links": {"next": nxt}})

    api.route("GET", "/items", by_url)
    conn = connection(api, endpoints=[{"entity_name": "items", "path": "/items", "records_path": "items", "pagination": {"type": "next_url", "next_url_path": "links.next"}}])
    assert [r["n"] for r in fetch(conn, "items", ["n"])] == [1, 2, 3]

    def by_header(r):
        page = int(r.query.get("page", 1))
        return json_resp([{"n": page}], headers={"Link": f'<{api.base}/things?page={page + 1}>; rel="next"'} if page < 3 else {"Link": '<https://x/y>; rel="prev"'})

    api.route("GET", "/things", by_header)
    conn2 = connection(api, endpoints=[{"entity_name": "things", "path": "/things", "pagination": {"type": "link_header"}}])
    assert [r["n"] for r in fetch(conn2, "things", ["n"])] == [1, 2, 3]


@pytest.mark.parametrize("target", ["https://evil.example.test:{port}/steal", "https://{name}:9/steal", "//evil.example.test/steal", "http://{name}:{port}/steal"])
def test_a_next_page_address_on_another_server_is_never_followed_and_no_credentials_go_there(api, target):
    address = target.format(port=api.port, name=NAME)
    api.route("GET", "/items", lambda r: json_resp({"items": [{"n": 1}], "links": {"next": address}}))
    conn = connection(
        api, auth={"type": "bearer"}, secrets={"token": SECRET_MARKER},
        endpoints=[{"entity_name": "items", "path": "/items", "records_path": "items", "pagination": {"type": "next_url", "next_url_path": "links.next"}}],
    )
    with pytest.raises(ConnectorError, match="different server") as caught:
        fetch(conn, "items", ["n"])
    assert SECRET_MARKER not in str(caught.value)
    assert [r.path for r in api.requests] == ["/items"]  # the address was refused before anything was sent to it


def test_a_server_that_ignores_the_paging_settings_does_not_loop(api):
    api.route("GET", "/items", lambda r: json_resp([{"n": 1}, {"n": 2}]))  # the same page for every page number
    conn = connection(api, endpoints=[{"entity_name": "items", "path": "/items", "pagination": {"type": "page", "max_pages": 50}}])
    assert [r["n"] for r in fetch(conn, "items", ["n"])] == [1, 2] and len(api.hits("/items")) == 2


# --- authentication ----------------------------------------------------------------------------------------------
def test_each_authentication_method_sends_the_credential_where_it_belongs(api):
    api.route("GET", "/invoices", lambda r: json_resp(INVOICES))
    cases = [
        ({"type": "api_key", "in": "header", "name": "X-API-Key"}, {"api_key": "k1"}, lambda r: r.headers["x-api-key"] == "k1"),
        ({"type": "api_key", "in": "query", "name": "key"}, {"api_key": "k2"}, lambda r: r.query["key"] == "k2"),
        ({"type": "bearer"}, {"token": "t3"}, lambda r: r.headers["authorization"] == "Bearer t3"),
        ({"type": "basic", "username": "alice"}, {"password": "pw"}, lambda r: r.headers["authorization"] == "Basic YWxpY2U6cHc="),
        ({"type": "none"}, {}, lambda r: "authorization" not in r.headers and "x-api-key" not in r.headers),
    ]
    for auth, secrets, check in cases:
        rest_connector._RECORDS.clear()
        api.requests.clear()
        fetch(connection(api, auth=auth, secrets=secrets))
        assert check(api.requests[0]), auth
        assert api.requests[0].headers["host"] == f"{NAME}:{api.port}"  # the name, not the vetted address, is what the server sees


def test_secrets_in_settings_are_filled_in_headers_query_and_body_at_request_time(api):
    api.route("POST", "/search", lambda r: json_resp([{"seen": r.headers["x-tenant"], "q": r.query["tenant"], "body": json.loads(r.body)["who"]}]))
    conn = connection(
        api, secrets={"tenant": "acme-corp"}, headers={"X-Tenant": "{{secret.tenant}}"},
        endpoints=[{"entity_name": "search", "method": "POST", "path": "/search", "query": {"tenant": "{{secret.tenant}}"}, "body": {"who": "{{secret.tenant}}"}}],
    )
    assert fetch(conn, "search", ["seen", "q", "body"]) == [{"seen": "acme-corp", "q": "acme-corp", "body": "acme-corp"}]
    assert "acme-corp" not in json.dumps(conn.connector_config)  # only the reference is stored


def test_a_setting_that_refers_to_a_missing_secret_says_which_without_showing_anything(api):
    api.route("GET", "/invoices", lambda r: json_resp(INVOICES))
    conn = connection(api, secrets={"tenant": "acme", "key": SECRET_MARKER}, headers={"X-Tenant": "{{secret.tenant}}"}, drop_secrets=("tenant",))
    with pytest.raises(ConnectorError, match="'tenant'") as caught:
        fetch(conn)
    assert SECRET_MARKER not in str(caught.value) and api.requests == []


OAUTH = {"type": "oauth2_client_credentials", "token_url": "PLACEHOLDER", "scope": "read"}


def _oauth_api(api, state):
    def token(r):
        state["token_calls"].append(parse_qs(r.body.decode()))
        return json_resp({"access_token": state["valid"], "expires_in": 3600}) if state.get("token_ok", True) else json_resp({"error": SECRET_MARKER}, 401)

    def data(r):
        return json_resp(INVOICES) if r.headers.get("authorization") == f"Bearer {state['valid']}" else json_resp({"error": "expired"}, 401)

    api.route("POST", "/oauth/token", token)
    api.route("GET", "/invoices", data)


def test_oauth2_client_credentials_fetches_a_token_caches_it_and_refreshes_it_once_when_rejected(api):
    state = {"valid": "tok-1", "token_calls": []}
    _oauth_api(api, state)
    conn = connection(api, auth={**OAUTH, "token_url": f"{api.base}/oauth/token"}, secrets={"client_id": "cid", "client_secret": SECRET_MARKER})
    assert len(fetch(conn)) == 2
    form = state["token_calls"][0]
    assert form["grant_type"] == ["client_credentials"] and form["client_id"] == ["cid"] and form["scope"] == ["read"]

    rest_connector._RECORDS.clear()  # a second read, but the token is reused: no second token request
    assert len(fetch(conn)) == 2 and len(state["token_calls"]) == 1

    state["valid"] = "tok-2"  # the token is revoked at the provider: one 401, one refresh, success
    rest_connector._RECORDS.clear()
    assert len(fetch(conn)) == 2 and len(state["token_calls"]) == 2


def test_oauth2_refresh_token_grant(api):
    state = {"valid": "tok-1", "token_calls": []}
    _oauth_api(api, state)
    conn = connection(
        api, auth={"type": "oauth2_refresh_token", "token_url": f"{api.base}/oauth/token"},
        secrets={"client_id": "cid", "client_secret": "cs", "refresh_token": "rt-123"},
    )
    assert len(fetch(conn)) == 2
    form = state["token_calls"][0]
    assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["rt-123"]


def test_a_token_service_that_refuses_is_reported_without_echoing_its_answer_or_the_secret(api):
    state = {"valid": "tok-1", "token_calls": [], "token_ok": False}
    _oauth_api(api, state)
    conn = connection(api, auth={**OAUTH, "token_url": f"{api.base}/oauth/token"}, secrets={"client_id": "cid", "client_secret": SECRET_MARKER})
    with pytest.raises(ConnectorError, match="token service refused") as caught:
        fetch(conn)
    assert SECRET_MARKER not in str(caught.value)


def test_a_persistently_rejected_token_does_not_loop(api):
    state = {"valid": "never-issued", "token_calls": []}
    _oauth_api(api, state)
    api.route("POST", "/oauth/token", lambda r: json_resp({"access_token": "something-else", "expires_in": 3600}))
    conn = connection(api, auth={**OAUTH, "token_url": f"{api.base}/oauth/token"}, secrets={"client_id": "c", "client_secret": "s"})
    with pytest.raises(ConnectorError, match="rejected the credentials"):
        fetch(conn)
    assert len(api.hits("/invoices")) == 2  # tried, refreshed once, tried again, gave up


# --- errors ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "status, expected",
    [(400, "rejected the request"), (401, "rejected the credentials"), (403, "rejected the credentials"), (404, "not found"), (429, "limiting"), (500, "reported an error"), (503, "reported an error")],
)
def test_an_error_from_the_api_is_reduced_to_its_status_and_never_echoes_its_body(api, status, expected):
    api.route("GET", "/invoices", lambda r: json_resp({"message": f"debug: your key was {SECRET_MARKER}", "trace": "/srv/app/internal.py"}, status))
    conn = connection(api, auth={"type": "bearer"}, secrets={"token": SECRET_MARKER})
    with pytest.raises(ConnectorError, match=expected) as caught:
        fetch(conn)
    assert SECRET_MARKER not in str(caught.value) and "internal.py" not in str(caught.value)


def test_test_reports_every_failing_endpoint_and_the_credentials_never_appear(api):
    api.route("GET", "/good", lambda r: json_resp(INVOICES))
    conn = connection(api, auth={"type": "bearer"}, secrets={"token": SECRET_MARKER}, endpoints=[
        {"entity_name": "good", "path": "/good"}, {"entity_name": "bad", "path": "/missing"},
    ])
    ok, detail = connectors.for_connection(conn).test(conn)
    assert ok is False and "'bad'" in detail and "not found" in detail and "'good'" not in detail and SECRET_MARKER not in detail
    api.route("GET", "/missing", lambda r: json_resp([]))
    ok, detail = connectors.for_connection(conn).test(conn)
    assert ok is True and "2 endpoint(s)" in detail


# --- the network policy applies to every call ------------------------------------------------------------------------
def test_a_redirect_is_never_followed(api):
    api.route("GET", "/invoices", lambda r: (302, {"Location": "https://169.254.169.254/latest/meta-data/"}, b""))
    with pytest.raises(ConnectorError, match="redirect"):
        fetch(connection(api))
    assert len(api.requests) == 1


def test_an_oversized_response_is_cut_off(api, monkeypatch):
    api.route("GET", "/invoices", lambda r: (200, {"Content-Type": "application/json"}, b"[" + b"1," * 5000 + b"1]"))
    monkeypatch.setitem(net_guard.safe_request.__kwdefaults__, "max_bytes", 1000)
    with pytest.raises(ConnectorError, match="larger than"):
        fetch(connection(api))


def test_a_name_that_resolves_to_an_internal_address_is_refused_before_any_connection(api, monkeypatch):
    monkeypatch.setattr(net_guard, "is_public_ip", REAL_IS_PUBLIC_IP)
    monkeypatch.setattr(net_guard, "_resolver", lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.9.8.7", port))])
    conn = connection(api, auth={"type": "bearer"}, secrets={"token": SECRET_MARKER})
    ok, detail = connectors.for_connection(conn).test(conn)
    assert ok is False and "public internet" in detail and "10.9.8.7" not in detail and SECRET_MARKER not in detail
    assert api.requests == []


def test_a_token_service_on_an_internal_address_is_refused_too(api, monkeypatch):
    monkeypatch.setattr(net_guard, "is_public_ip", lambda ip: str(ip) != "10.9.8.7")
    calls = []

    def resolver(host, port):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.9.8.7" if host == "auth.internal.example" else "127.0.0.1", port))]

    monkeypatch.setattr(net_guard, "_resolver", resolver)
    conn = connection(api, auth={"type": "oauth2_client_credentials", "token_url": f"https://auth.internal.example:{api.port}/token"}, secrets={"client_id": "c", "client_secret": SECRET_MARKER})
    with pytest.raises(ConnectorError, match="public internet") as caught:
        fetch(conn)
    assert SECRET_MARKER not in str(caught.value) and api.requests == []


def test_a_certificate_that_is_not_trusted_is_refused(api, monkeypatch):
    monkeypatch.setattr(net_guard, "_default_verify", lambda: ssl.create_default_context())  # the real trust store, which does not know the test CA
    with pytest.raises(ConnectorError, match="certificate"):
        fetch(connection(api))


# --- XML and SOAP --------------------------------------------------------------------------------------------------------
INVOICES_XML = (
    '<Invoices xmlns="urn:acme"><Invoice id="1"><Amount>10.50</Amount><Customer><Name>Acme</Name></Customer></Invoice>'
    '<Invoice id="2"><Amount>7</Amount><Customer><Name>Beta</Name></Customer></Invoice></Invoices>'
)


def test_an_xml_response_from_a_rest_endpoint_is_read_like_json(api):
    api.route("GET", "/invoices", lambda r: xml_resp(INVOICES_XML))
    conn = connection(api, endpoints=[{"entity_name": "invoices", "path": "/invoices", "records_path": "Invoices.Invoice"}])
    [entity] = connectors.for_connection(conn).discover(conn)
    assert {f.field_name: f.data_type for f in entity.fields} == {"@id": "integer", "Amount": "double", "Customer.Name": "string"}
    assert fetch(conn, fields=["@id", "Amount", "Customer.Name"]) == [
        {"@id": 1, "Amount": 10.5, "Customer.Name": "Acme"}, {"@id": 2, "Amount": 7.0, "Customer.Name": "Beta"},
    ]


def test_a_single_xml_element_is_one_record(api):
    api.route("GET", "/invoices", lambda r: xml_resp('<Invoices><Invoice id="9"><Amount>1</Amount></Invoice></Invoices>'))
    conn = connection(api, endpoints=[{"entity_name": "invoices", "path": "/invoices", "records_path": "Invoices.Invoice"}])
    assert fetch(conn, fields=["@id"]) == [{"@id": 9}]


ENVELOPE = (
    '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><GetInvoices><Account>{{secret.account}}</Account>'
    "</GetInvoices></soap:Body></soap:Envelope>"
)
SOAP_OK = (
    '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><GetInvoicesResponse>'
    "<Invoice><Number>1</Number><Total>10.5</Total></Invoice><Invoice><Number>2</Number><Total>7</Total></Invoice>"
    "</GetInvoicesResponse></soap:Body></soap:Envelope>"
)


def _soap_conn(api, **kw):
    return connection(
        api, kind="soap_api", secrets={"account": "ACC-42"},
        endpoints=[{
            "entity_name": "invoices", "path": "/svc", "body": ENVELOPE, "soap_action": "urn:GetInvoices",
            "records_path": "Envelope.Body.GetInvoicesResponse.Invoice",
        }], **kw,
    )


def test_a_soap_call_sends_the_envelope_with_its_secret_filled_in_and_reads_the_records(api):
    api.route("POST", "/svc", lambda r: xml_resp(SOAP_OK, content_type="text/xml; charset=utf-8"))
    conn = _soap_conn(api)
    rows = fetch(conn, fields=["Number", "Total"])
    assert rows == [{"Number": 1, "Total": 10.5}, {"Number": 2, "Total": 7.0}]
    sent = api.requests[0]
    assert sent.headers["soapaction"] == '"urn:GetInvoices"' and sent.headers["content-type"].startswith("text/xml")
    assert b"<Account>ACC-42</Account>" in sent.body and "ACC-42" not in json.dumps(conn.connector_config)


def test_a_soap_fault_is_reported_with_its_reason(api):
    fault = (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><soap:Fault><faultcode>soap:Client</faultcode>'
        "<faultstring>Invalid account number</faultstring></soap:Fault></soap:Body></soap:Envelope>"
    )
    api.route("POST", "/svc", lambda r: xml_resp(fault, status=500, content_type="text/xml"))
    with pytest.raises(ConnectorError, match="SOAP fault: Invalid account number"):
        fetch(_soap_conn(api))


@pytest.mark.parametrize(
    "payload",
    [
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><x>&xxe;</x>',
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]><lolz>&lol2;</lolz>',
        '<!DOCTYPE x SYSTEM "http://169.254.169.254/evil.dtd"><x/>',
    ],
)
def test_xml_that_uses_dtds_or_entities_is_refused_and_nothing_is_fetched_or_expanded(api, payload):
    api.route("GET", "/invoices", lambda r: xml_resp(payload))
    conn = connection(api, endpoints=[{"entity_name": "invoices", "path": "/invoices", "records_path": "x"}])
    with pytest.raises(ConnectorError, match="not accepted") as caught:
        fetch(conn)
    assert "root:" not in str(caught.value) and len(api.requests) == 1  # no file content, and no second request for a DTD


def test_malformed_and_absurdly_deep_xml_are_refused(api):
    api.route("GET", "/invoices", lambda r: xml_resp("<a><b></a>"))
    with pytest.raises(ConnectorError, match="not valid XML"):
        fetch(connection(api))
    api.route("GET", "/invoices", lambda r: xml_resp("<a>" * 500 + "</a>" * 500))
    with pytest.raises(ConnectorError, match="nested too deeply"):
        fetch(connection(api))


# --- caching and one connection's failure ---------------------------------------------------------------------------------
def test_repeated_reads_within_a_minute_call_the_api_once(api):
    api.route("GET", "/invoices", lambda r: json_resp(INVOICES))
    conn = connection(api)
    fetch(conn, fields=["id"])
    fetch(conn, fields=["id", "amount"])
    fetch(conn, fields=["id"], limit=1)
    assert len(api.hits("/invoices")) == 1
    rest_connector.forget(conn.connection_id)
    fetch(conn, fields=["id"])
    assert len(api.hits("/invoices")) == 2


def test_a_larger_read_than_the_cached_one_goes_back_to_the_api(api):
    data = _numbered(50)
    api.route("GET", "/items", lambda r: json_resp(data))
    conn = connection(api, endpoints=[{"entity_name": "items", "path": "/items", "pagination": {"type": "page", "max_pages": 1}}])
    assert len(fetch(conn, "items", ["n"], limit=10)) == 10  # cached as "10 of possibly more"
    assert len(fetch(conn, "items", ["n"], limit=40)) == 40  # so a bigger read must ask again
    assert len(api.hits("/items")) == 2


def test_an_empty_endpoint_is_an_empty_table(api):
    api.route("GET", "/invoices", lambda r: json_resp([]))
    conn = connection(api)
    [entity] = connectors.for_connection(conn).discover(conn)
    assert entity.fields == [] and "0 record" in entity.description
    assert fetch(conn, fields=["id"]) == []


# --- what the vendor APIs need, in general form ---------------------------------------------------------------------------
def test_a_key_that_contains_dots_is_found_as_written():
    doc = {"value": [{"n": 1}], "@odata.nextLink": "/next?page=2", "a": {"b.c": {"d": 5}}, "list": [{"x": 1}, {"x": 2}]}
    assert rest_connector.dig(doc, "@odata.nextLink") == "/next?page=2"
    assert rest_connector.dig(doc, "a.b.c.d") == 5
    assert rest_connector.dig(doc, "list.1.x") == 2
    assert rest_connector.dig(doc, "value.0.n") == 1
    assert rest_connector.dig(doc, "nope") is None and rest_connector.dig(doc, "list.7") is None and rest_connector.dig(doc, "a.zzz") is None


def test_an_empty_answer_means_no_more_records_not_an_error(api):
    def handler(r):
        page = int(r.query.get("page", 1))
        return json_resp([{"n": page}]) if page <= 2 else (204, {}, b"")  # some APIs answer "204 No Content" when the records run out

    api.route("GET", "/items", handler)
    conn = connection(api, endpoints=[{"entity_name": "items", "path": "/items", "pagination": {"type": "page"}}])
    assert [r["n"] for r in fetch(conn, "items", ["n"])] == [1, 2]


def test_an_odata_style_api_pages_through_a_key_with_a_dot_in_it(api):
    def handler(r):
        page = int(r.query.get("page", 1))
        return json_resp({"value": [{"n": page}], **({"@odata.nextLink": f"{api.base}/things?page={page + 1}"} if page < 3 else {})})

    api.route("GET", "/things", handler)
    conn = connection(api, endpoints=[{"entity_name": "things", "path": "/things", "records_path": "value", "pagination": {"type": "next_url", "next_url_path": "@odata.nextLink"}}])
    assert [r["n"] for r in fetch(conn, "things", ["n"])] == [1, 2, 3]


def test_the_authorization_scheme_can_be_something_other_than_bearer(api):
    api.route("GET", "/invoices", lambda r: json_resp(INVOICES))
    fetch(connection(api, auth={"type": "bearer", "scheme": "Zoho-oauthtoken"}, secrets={"token": "abc"}))
    assert api.requests[0].headers["authorization"] == "Zoho-oauthtoken abc"

    state = {"valid": "tok-1", "token_calls": []}
    api.requests.clear()
    rest_connector._RECORDS.clear()

    def token(r):
        state["token_calls"].append(1)
        return json_resp({"access_token": "tok-1", "expires_in": 3600})

    api.route("POST", "/oauth/token", token)
    fetch(connection(api, auth={"type": "oauth2_client_credentials", "token_url": f"{api.base}/oauth/token", "scheme": "Zoho-oauthtoken"}, secrets={"client_id": "c", "client_secret": "s"}))
    assert api.hits("/invoices")[-1].headers["authorization"] == "Zoho-oauthtoken tok-1"


# --- over the real routes, with real users and roles, against the real database -----------------------------------------
def _http():
    from fastapi.testclient import TestClient

    from app.main import app

    # A real client address: audit_logs.ip_address is an INET column and rejects Starlette's default host.
    return TestClient(app, client=("127.0.0.1", 50000))


def _bearer(user):
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id=user.user_id, organization_id=user.organization_id)}"}


def _create_rest(http, world, api, **overrides):
    body = {
        "db_type": "rest_api", "connection_name": "Billing API",
        "config": {
            "base_url": api.base, "auth": {"type": "api_key", "in": "header", "name": "X-API-Key"},
            "endpoints": [{"entity_name": "invoices", "path": "/invoices"}],
        },
        "secrets": {"api_key": SECRET_MARKER},
    }
    body.update(overrides)
    return http.post(f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector", json=body, headers=_bearer(world.manager))


def test_a_rest_connection_end_to_end_over_the_routes(db, connector_world, api):
    from app.models.data_source import DataConnection
    from app.services import data_source_service

    http, world = _http(), connector_world
    api.route("GET", "/invoices", lambda r: json_resp(INVOICES) if r.headers.get("x-api-key") == SECRET_MARKER else json_resp({"error": "no"}, 401))

    created = _create_rest(http, world, api)
    assert created.status_code == 201, created.text
    assert SECRET_MARKER not in created.text  # a credential is never returned, not even to the person who supplied it
    out = created.json()
    assert out["host"] == NAME and out["port"] == api.port and out["connector_config"]["endpoints"][0]["entity_name"] == "invoices"
    row = db.get(DataConnection, uuid.UUID(out["connection_id"]))
    assert SECRET_MARKER not in row.encrypted_password and connector_settings.unpack_secrets(row.encrypted_password) == {"api_key": SECRET_MARKER}

    tested = http.post(f"/api/v1/connections/{out['connection_id']}/test", headers=_bearer(world.manager)).json()
    assert tested["success"] is True and "1 endpoint" in tested["detail"] and SECRET_MARKER not in json.dumps(tested)
    db.expire_all()
    assert db.get(DataConnection, uuid.UUID(out["connection_id"])).connection_status == "connected"

    discovered = http.post(f"/api/v1/connections/{out['connection_id']}/discover", headers=_bearer(world.manager))
    assert discovered.status_code == 200, discovered.text
    [entity] = data_source_service.list_entities(db, data_source_id=world.source.data_source_id)
    assert (entity.entity_name, entity.entity_type) == ("invoices", "api")
    assert {f.field_name for f in data_source_service.list_fields(db, entity_id=entity.entity_id)} == {"id", "amount", "customer.name", "customer.tier", "tags"}
    db.expire_all()
    rows = data_source_service.fetch_direct_records(db.get(DataConnection, uuid.UUID(out["connection_id"])), entity_name="invoices", field_names=["id", "amount"], limit=10)
    assert rows == [{"id": "A1", "amount": 10.5}, {"id": "A2", "amount": 7.0}]

    # The same server, path and settings again is a duplicate; different settings are a different connection.
    assert _create_rest(http, world, api).status_code == 400
    other = _create_rest(http, world, api, config={**out["connector_config"], "endpoints": [{"entity_name": "credit_notes", "path": "/credit_notes"}]})
    assert other.status_code == 201, other.text


def test_unsafe_or_malformed_rest_settings_are_refused_at_creation(connector_world, api):
    http, world = _http(), connector_world
    base = {"base_url": api.base, "auth": {"type": "none"}, "endpoints": [{"entity_name": "t", "path": "/t"}]}

    def create(**changes):
        return _create_rest(http, world, api, config={**base, **changes}, secrets={})

    for label, response in {
        "internal address": create(base_url="https://10.0.0.5/api"),
        "metadata address": create(base_url="https://169.254.169.254/latest"),
        "plain http": create(base_url="http://api.example.com"),
        "absolute path": create(endpoints=[{"entity_name": "t", "path": "https://evil.example/x"}]),
        "inline credential": create(headers={"Authorization": "Bearer abc123"}),
        "unknown setting": create(proxy="http://x"),
    }.items():
        assert response.status_code == 400, (label, response.text)
    assert "credential" in create(headers={"Authorization": "Bearer abc123"}).json()["detail"]
    assert _create_rest(http, world, api, secrets={}).status_code == 400  # api_key authentication without the key


def test_only_managers_of_the_same_organization_can_use_an_api_connection(connector_world, api):
    http, world = _http(), connector_world
    api.route("GET", "/invoices", lambda r: json_resp(INVOICES))
    cid = _create_rest(http, world, api).json()["connection_id"]
    for user in (world.read_only, world.outsider):
        assert http.post(f"/api/v1/connections/{cid}/test", headers=_bearer(user)).status_code == 403
        assert http.post(f"/api/v1/connections/{cid}/discover", headers=_bearer(user)).status_code == 403
        assert _create_rest(http, SimpleNamespace(source=world.source, manager=user), api).status_code == 403
    assert http.get(f"/api/v1/data-sources/{world.source.data_source_id}/connections", headers=_bearer(world.outsider)).status_code == 403
    assert api.requests == []  # none of the refused calls reached the API


def test_editing_an_api_connection_goes_through_approval_and_keeps_the_host_in_step(db, connector_world, connector_approver, api):
    from app.models.data_source import DataConnection

    http, world = _http(), connector_world
    cid = _create_rest(http, world, api).json()["connection_id"]
    row = db.get(DataConnection, uuid.UUID(cid))
    row.connection_status = "connected"
    db.commit()

    # Rotating the key and adding an endpoint: validated like creation; nothing changes until someone else approves.
    edited = http.post(
        f"/api/v1/connections/{cid}/changes/update", headers=_bearer(world.manager),
        json={
            "secrets": {"api_key": "rotated-key-99"},
            "connector_config": {"endpoints": [{"entity_name": "invoices", "path": "/invoices"}, {"entity_name": "vendors", "path": "/vendors"}]},
        },
    )
    assert edited.status_code == 202, edited.text
    assert "rotated-key-99" not in edited.text
    db.expire_all()
    assert connector_settings.unpack_secrets(db.get(DataConnection, uuid.UUID(cid)).encrypted_password) == {"api_key": SECRET_MARKER}
    approved = http.post(f"/api/v1/connections/{cid}/changes/{edited.json()['change_id']}/approve", headers=_bearer(connector_approver))
    assert approved.status_code == 200, approved.text
    db.expire_all()
    row = db.get(DataConnection, uuid.UUID(cid))
    assert connector_settings.unpack_secrets(row.encrypted_password) == {"api_key": "rotated-key-99"}
    assert [e["entity_name"] for e in row.connector_config["endpoints"]] == ["invoices", "vendors"]
    assert row.connection_status == "pending"  # new credentials or endpoints are trusted only after a test

    # A new base URL moves the host column with it; a host or port set directly is refused for an API.
    moved = http.post(f"/api/v1/connections/{cid}/changes/update", headers=_bearer(world.manager), json={"connector_config": {"base_url": "https://api2.example.com/v2"}})
    assert moved.status_code == 202, moved.text
    assert moved.json()["proposed_changes"]["host"] == "api2.example.com"
    http.post(f"/api/v1/connections/{cid}/changes/{moved.json()['change_id']}/cancel", json={"reason": "test cleanup"}, headers=_bearer(world.manager))
    assert http.post(f"/api/v1/connections/{cid}/changes/update", headers=_bearer(world.manager), json={"host": "elsewhere.example.com"}).status_code == 400
    assert http.post(f"/api/v1/connections/{cid}/changes/update", headers=_bearer(world.manager), json={"connector_config": {"headers": {"Authorization": "Bearer literal123"}}}).status_code == 400


# --- the vendor templates, against servers that answer the way each vendor documents ------------------------------------
# NOT a live Salesforce / Zoho / Dynamics: these prove the expanded settings drive the connector correctly
# (token request, headers, query, paging, response shape), not that a real tenant accepts them.
def _preset_connection(api, key, params, secrets, patch):
    from app.services.connectors import presets

    config = presets.expand(key, params)
    patch(config)  # the vendors' own token/API hosts are not reachable here: point the test-only copy at the local server
    prepared = connector_settings.prepare("rest_api", config, secrets)
    return SimpleNamespace(
        connection_id=uuid.uuid4(), db_type="rest_api", connector_config=prepared.config, encrypted_password=connector_settings.pack_secrets(prepared.secrets),
        host=prepared.host, port=prepared.port, username=None,
    )


def test_salesforce_template_queries_and_follows_next_records_url(api):
    state = {"tokens": []}

    def token(r):
        state["tokens"].append(parse_qs(r.body.decode()))
        return json_resp({"access_token": "sf-token", "instance_url": api.base, "token_type": "Bearer"})

    def query(r):
        assert r.headers["authorization"] == "Bearer sf-token"
        if r.query.get("q"):
            return json_resp({
                "totalSize": 3, "done": False, "nextRecordsUrl": "/services/data/v60.0/query/01gXX-2000",
                "records": [{"attributes": {"type": "Account", "url": "/x/1"}, "Id": "001A", "Name": "Acme", "AnnualRevenue": 1000.5},
                            {"attributes": {"type": "Account", "url": "/x/2"}, "Id": "001B", "Name": "Beta", "AnnualRevenue": None}],
            })
        return json_resp({"totalSize": 3, "done": True, "records": [{"attributes": {"type": "Account", "url": "/x/3"}, "Id": "001C", "Name": "Gamma", "AnnualRevenue": 7}]})

    api.route("POST", "/services/oauth2/token", token)
    api.route("GET", "/services/data/v60.0/query", query)
    api.route("GET", "/services/data/v60.0/query/01gXX-2000", query)

    def patch(config):
        config["auth"]["token_url"] = f"{api.base}/services/oauth2/token"

    conn = _preset_connection(api, "salesforce", {"instance_url": api.base, "objects": ["Account"]}, {"client_id": "cid", "client_secret": "cs", "refresh_token": "rt"}, patch)
    assert state["tokens"] == []
    rows = fetch(conn, "Account", ["Id", "Name", "AnnualRevenue", "attributes.type"])
    assert [r["Id"] for r in rows] == ["001A", "001B", "001C"] and rows[0]["attributes.type"] == "Account" and rows[0]["AnnualRevenue"] == 1000.5
    assert state["tokens"][0]["grant_type"] == ["refresh_token"] and state["tokens"][0]["refresh_token"] == ["rt"]
    assert api.hits("/services/data/v60.0/query")[0].query["q"] == "SELECT FIELDS(STANDARD) FROM Account"


def test_zoho_template_sends_the_zoho_token_scheme_pages_and_treats_204_as_the_end(api):
    def leads(r):
        assert r.headers["authorization"] == "Zoho-oauthtoken zoho-token" and "Lead_Status" in r.query["fields"] and r.query["per_page"] == "200"
        page = int(r.query["page"])
        if page == 1:
            return json_resp({"data": [{"id": "1", "Last_Name": "Ng", "Email": "ng@example.com"}, {"id": "2", "Last_Name": "Ito", "Email": None}], "info": {"more_records": True}})
        return 204, {}, b""  # Zoho answers 204 No Content when the records run out

    api.route("POST", "/oauth/v2/token", lambda r: json_resp({"access_token": "zoho-token", "expires_in": 3600}))
    api.route("GET", "/crm/v6/Leads", leads)

    def patch(config):
        config["auth"]["token_url"] = f"{api.base}/oauth/v2/token"
        config["base_url"] = f"{api.base}/crm/v6"

    conn = _preset_connection(api, "zoho_crm", {"modules": ["Leads"]}, {"client_id": "c", "client_secret": "s", "refresh_token": "r"}, patch)
    assert [r["Last_Name"] for r in fetch(conn, "Leads", ["Last_Name"])] == ["Ng", "Ito"]


def test_dynamics_template_uses_client_credentials_odata_headers_and_next_link(api):
    state = {"tokens": []}

    def token(r):
        state["tokens"].append(parse_qs(r.body.decode()))
        return json_resp({"access_token": "dyn-token", "expires_in": 3599})

    def accounts(r):
        assert r.headers["authorization"] == "Bearer dyn-token" and r.headers["prefer"] == "odata.maxpagesize=500" and r.headers["odata-version"] == "4.0"
        if "$skiptoken" not in r.query:
            return json_resp({"value": [{"accountid": "a1", "name": "Acme", "revenue": 10.5}], "@odata.nextLink": f"{api.base}/api/data/v9.2/accounts?$skiptoken=tok2"})
        return json_resp({"value": [{"accountid": "a2", "name": "Beta", "revenue": 3}]})

    api.route("POST", f"/{TENANT}/oauth2/v2.0/token", token)
    api.route("GET", "/api/data/v9.2/accounts", accounts)

    def patch(config):
        config["auth"]["token_url"] = f"{api.base}/{TENANT}/oauth2/v2.0/token"

    conn = _preset_connection(api, "dynamics_crm", {"environment_url": api.base, "tenant_id": TENANT, "entities": ["accounts"]}, {"client_id": "c", "client_secret": "s"}, patch)
    assert [r["name"] for r in fetch(conn, "accounts", ["name", "revenue"])] == ["Acme", "Beta"]
    form = state["tokens"][0]
    assert form["grant_type"] == ["client_credentials"] and form["scope"] == [f"{api.base}/.default"]


TENANT = "0f3b2a1c-9d8e-4f7a-b6c5-123456789abc"


def test_a_preset_can_be_used_to_create_a_connection_over_the_routes_and_is_listed_for_the_ui(connector_world, api):
    http, world = _http(), connector_world
    listed = http.get("/api/v1/connector-presets", headers=_bearer(world.read_only))
    assert listed.status_code == 200 and [p["key"] for p in listed.json()] == ["salesforce", "zoho_crm", "dynamics_crm"]
    assert http.get("/api/v1/connector-presets").status_code in (401, 403)

    url = f"/api/v1/data-sources/{world.source.data_source_id}/connections/connector"
    secrets = {"client_id": "cid", "client_secret": SECRET_MARKER, "refresh_token": "rt"}
    created = http.post(url, json={"db_type": "rest_api", "preset": "salesforce", "preset_params": {"instance_url": api.base, "objects": ["Account"]}, "secrets": secrets}, headers=_bearer(world.manager))
    assert created.status_code == 201, created.text
    assert SECRET_MARKER not in created.text and created.json()["connector_config"]["preset"] == "salesforce" and created.json()["host"] == NAME

    def refused(**body):
        return http.post(url, json={"db_type": "rest_api", "secrets": secrets, **body}, headers=_bearer(world.manager))

    assert refused(preset="salesforce", preset_params={"instance_url": api.base}, config={"base_url": api.base}).status_code == 400  # a preset or settings, not both
    assert refused(preset_params={"instance_url": api.base}).status_code == 400  # parameters without a preset
    assert refused(preset="nonexistent").status_code == 400
    assert refused(preset="salesforce", preset_params={"instance_url": "https://10.0.0.5"}).status_code == 400
    assert http.post(url, json={"db_type": "sftp", "preset": "salesforce", "preset_params": {"instance_url": api.base}}, headers=_bearer(world.manager)).status_code == 400


# --- hardening found on review: nothing may hold the platform's scheduler loop for long ------------------------------------------
def test_a_response_that_takes_too_long_in_total_is_cut_off(api, monkeypatch):
    api.route("GET", "/invoices", lambda r: json_resp(INVOICES))
    monkeypatch.setattr(net_guard, "TOTAL_TIMEOUT_SECONDS", 0.0)  # every read is "late": proves the ceiling on the WHOLE answer is enforced
    with pytest.raises(ConnectorError, match="too long"):
        fetch(connection(api))


def test_reading_many_pages_has_an_overall_time_budget(api, monkeypatch):
    api.route("GET", "/items", lambda r: json_resp([{"n": int(r.query.get("page", 1))}]))
    conn = connection(api, endpoints=[{"entity_name": "items", "path": "/items", "pagination": {"type": "page", "max_pages": 50}}])
    monkeypatch.setattr(rest_connector, "READ_BUDGET_SECONDS", 0)
    with pytest.raises(ConnectorError, match="too slow"):
        fetch(conn, "items", ["n"])
    assert len(api.hits("/items")) <= 1  # it stopped instead of walking every page


def test_the_default_limits_do_not_get_in_the_way_of_a_normal_read(api):
    api.route("GET", "/invoices", lambda r: json_resp(INVOICES))
    assert net_guard.TOTAL_TIMEOUT_SECONDS >= 30 and rest_connector.READ_BUDGET_SECONDS >= 60
    assert len(fetch(connection(api), fields=["id"])) == 2


def test_an_api_table_is_mapped_through_the_real_routes_approved_by_someone_else_and_a_rule_runs_on_it(db, connector_world, connector_approver, api):
    """The same path as for an uploaded file, for an API endpoint whose records have nested fields (which become dotted
    column names): connect -> test -> discover -> read columns -> suggestions -> maker -> a different approver -> run."""
    import json as jsonlib

    from app.models.audit_test import AuditTest, TestRule
    from app.models.data_source import DataConnection
    from app.models.monitoring import MonitoringSchedule
    from app.models.rbac import Role, User, UserRole
    from app.services import data_source_service, direct_execution_service

    http, world = _http(), connector_world
    statuses = ["active", "active", "locked", "terminated", "active", "active", "terminated", "active", "locked", "active"]
    users = [
        {"user_id": f"U{i:03d}", "username": f"user{i}", "status": s, "last_login": f"2026-08-{i + 1:02d}", "profile": {"department": "Finance" if i % 2 else "IT"}}
        for i, s in enumerate(statuses)
    ]
    api.route("GET", "/users", lambda r: json_resp({"data": users}) if r.headers.get("x-api-key") == SECRET_MARKER else json_resp({"error": "no"}, 401))

    maker = User(
        organization_id=world.org.organization_id, first_name="Map", last_name="Maker", email=f"map-maker-{uuid.uuid4().hex}@test.local",
        password_hash="not-a-real-hash", status="active",
    )
    db.add(maker)
    db.flush()
    db.add(UserRole(user_id=maker.user_id, role_id=db.query(Role).filter(Role.role_name == "Auditor").one().role_id))
    db.commit()
    db.refresh(maker)
    test = None
    try:
        created = _create_rest(
            http, world, api,
            config={"base_url": api.base, "auth": {"type": "api_key", "in": "header", "name": "X-API-Key"}, "endpoints": [{"entity_name": "users", "path": "/users", "records_path": "data"}]},
        )
        assert created.status_code == 201, created.text
        connection_id = created.json()["connection_id"]
        assert http.post(f"/api/v1/connections/{connection_id}/test", headers=_bearer(world.manager)).json()["success"] is True
        assert http.post(f"/api/v1/connections/{connection_id}/discover", headers=_bearer(world.manager)).status_code == 200
        [entity] = data_source_service.list_entities(db, data_source_id=world.source.data_source_id)
        assert (entity.entity_name, entity.entity_type) == ("users", "api")
        assert {f.field_name for f in data_source_service.list_fields(db, entity_id=entity.entity_id)} == {"user_id", "username", "status", "last_login", "profile.department"}
        connections = data_source_service._connected_direct_connections(db, world.source.data_source_id)
        assert data_source_service.profile_entity_columns(db, entity_id=entity.entity_id, connections=connections) is not None

        suggestions = http.get(f"/api/v1/data-sources/entities/{entity.entity_id}/mapping-suggestions", headers=_bearer(maker))
        assert suggestions.status_code == 200, suggestions.text
        by_column = {s["field_name"]: s for s in suggestions.json()}
        assert by_column["status"]["suggested_canonical_field"] == "user.status" and by_column["username"]["suggested_canonical_field"] == "user.username"
        assert by_column["status"]["value_fit_reason"] is None

        test = AuditTest(organization_id=world.org.organization_id, test_name="terminated accounts (api)")
        db.add(test)
        db.flush()
        db.add(TestRule(
            audit_test_id=test.audit_test_id, rule_name="status is terminated", rule_type="threshold", status="active",
            rule_definition=jsonlib.dumps({"rule_type": "threshold", "object": "user", "field": "status", "operator": "eq", "value": "terminated"}),
        ))
        db.add(MonitoringSchedule(audit_test_id=test.audit_test_id, frequency="daily", status="active", is_active=True))
        db.commit()
        field = by_column["status"]
        mapped = http.post(
            f"/api/v1/organizations/{world.org.organization_id}/audit-tests/{test.audit_test_id}/data-mappings",
            json={
                "data_source_id": str(world.source.data_source_id), "entity_id": str(entity.entity_id), "field_id": field["field_id"],
                "canonical_field": field["suggested_canonical_field"], "confidence_score": field["confidence_score"],
            },
            headers=_bearer(maker),
        )
        assert mapped.status_code == 201, mapped.text
        mapping_id = mapped.json()["mapping_id"]
        assert http.post(f"/api/v1/data-mappings/{mapping_id}/approve", headers=_bearer(maker)).status_code == 403
        assert http.post(f"/api/v1/data-mappings/{mapping_id}/approve", headers=_bearer(connector_approver)).status_code == 200

        rest_connector._RECORDS.clear()
        mine = [d for d in direct_execution_service._resolve_due_direct_tests(db) if d.audit_test_id == test.audit_test_id]
        assert len(mine) == 1
        report = direct_execution_service._run_one(mine[0])
        assert report.error_message is None
        assert report.records_analyzed == 10 and len(report.exceptions) == 2
        assert SECRET_MARKER not in jsonlib.dumps([e.model_dump(mode="json") for e in report.exceptions], default=str)
        assert db.get(DataConnection, uuid.UUID(connection_id)).connection_status == "connected"
    finally:
        db.rollback()
        if test is not None:
            db.delete(db.get(AuditTest, test.audit_test_id))
            db.commit()
        db.delete(db.get(User, maker.user_id))
        db.commit()


# --- a provider that rotates the refresh token on every use -------------------------------------------------------------------
class RotatingProvider:
    """Behaves like a provider that invalidates a refresh token the moment it is used and returns a new one. A client that
    ever sends a token twice, or an old one, is refused."""

    def __init__(self, first_refresh_token="rt-1"):
        self.valid_refresh = first_refresh_token
        self.access = None
        self.sent: list[str] = []
        self.count = 0

    def token(self, request):
        form = parse_qs(request.body.decode())
        sent = form.get("refresh_token", [""])[0]
        self.sent.append(sent)
        if sent != self.valid_refresh:
            return json_resp({"error": "invalid_grant"}, 400)
        self.count += 1
        self.valid_refresh = f"rt-{self.count + 1}"
        self.access = f"at-{self.count}"
        return json_resp({"access_token": self.access, "expires_in": 3600, "refresh_token": self.valid_refresh})

    def data(self, request):
        return json_resp(INVOICES) if request.headers.get("authorization") == f"Bearer {self.access}" else json_resp({"error": "expired"}, 401)


def _rotating_setup(api):
    provider = RotatingProvider()
    api.route("POST", "/oauth/token", provider.token)
    api.route("GET", "/invoices", provider.data)
    config = {
        "base_url": api.base, "auth": {"type": "oauth2_refresh_token", "token_url": f"{api.base}/oauth/token"},
        "headers": {"X-Tenant": "{{secret.tenant}}"}, "endpoints": [{"entity_name": "invoices", "path": "/invoices"}],
    }
    secrets = {"client_id": "cid", "client_secret": "csecret", "refresh_token": "rt-1", "tenant": "acme"}
    return provider, config, secrets


def test_a_rotated_refresh_token_is_stored_and_the_next_refresh_uses_it_even_from_a_stale_copy(db, connector_world, api):
    from app.models.data_source import DataConnection
    from app.services import data_source_service

    http, world = _http(), connector_world
    provider, config, secrets = _rotating_setup(api)
    created = _create_rest(http, world, api, config=config, secrets=secrets)
    assert created.status_code == 201, created.text
    connection_id = uuid.UUID(created.json()["connection_id"])

    def stored():
        db.expire_all()
        return connector_settings.unpack_secrets(db.get(DataConnection, connection_id).encrypted_password)

    row = db.get(DataConnection, connection_id)
    stale = SimpleNamespace(  # a copy of the connection as it was BEFORE any rotation, like one a scheduler cycle holds
        connection_id=row.connection_id, db_type=row.db_type, connector_config=row.connector_config, encrypted_password=row.encrypted_password,
        host=row.host, port=row.port, username=row.username,
    )

    assert len(data_source_service.fetch_direct_records(row, entity_name="invoices", field_names=["id"], limit=10)) == 2
    assert provider.sent == ["rt-1"]
    after_first = stored()
    assert after_first["refresh_token"] == "rt-2"  # the new token was kept...
    assert {k: v for k, v in after_first.items() if k != "refresh_token"} == {"client_id": "cid", "client_secret": "csecret", "tenant": "acme"}  # ...and nothing else touched

    # The access token expires. The next refresh comes from the STALE copy (which still holds rt-1, now dead at the
    # provider): it must use what is stored, or the provider refuses it and the connection is lost.
    rest_connector._TOKENS.clear()
    rest_connector._RECORDS.clear()
    assert len(data_source_service.fetch_direct_records(stale, entity_name="invoices", field_names=["id"], limit=10)) == 2
    assert provider.sent == ["rt-1", "rt-2"]  # never a token twice, never an old one
    assert stored()["refresh_token"] == "rt-3"

    # While the access token is still good, nothing is refreshed at all (so nothing rotates needlessly).
    rest_connector._RECORDS.clear()
    assert len(data_source_service.fetch_direct_records(row, entity_name="invoices", field_names=["id"], limit=10)) == 2
    assert provider.sent == ["rt-1", "rt-2"]
    listed = http.get(f"/api/v1/data-sources/{world.source.data_source_id}/connections", headers=_bearer(world.manager)).text
    assert "rt-1" not in listed and "rt-2" not in listed and "rt-3" not in listed and "csecret" not in listed  # no token is ever returned by the API


def test_a_person_who_replaces_the_refresh_token_by_hand_wins_over_an_older_stored_one(db, connector_world, api):
    """A new credential typed in (through the approved edit) is the one to use, not a stale rotated token."""
    from app.models.data_source import DataConnection
    from app.services import data_source_service

    http, world = _http(), connector_world
    provider, config, secrets = _rotating_setup(api)
    provider.valid_refresh = "rt-hand-typed"  # the provider has been re-authorized: only the new token works now
    created = _create_rest(http, world, api, config=config, secrets={**secrets, "refresh_token": "rt-hand-typed"})
    assert created.status_code == 201, created.text
    row = db.get(DataConnection, uuid.UUID(created.json()["connection_id"]))
    assert len(data_source_service.fetch_direct_records(row, entity_name="invoices", field_names=["id"], limit=10)) == 2
    assert provider.sent == ["rt-hand-typed"]


def test_a_provider_that_does_not_rotate_causes_no_write_and_client_credentials_never_read_the_store(api, monkeypatch):
    writes, reads = [], []
    monkeypatch.setattr(rest_connector, "_store_refresh_token", lambda cid, token: writes.append(token) or True)
    monkeypatch.setattr(rest_connector, "_stored_secrets", lambda cid: reads.append(cid) or None)
    api.route("POST", "/oauth/token", lambda r: json_resp({"access_token": "tok", "expires_in": 3600}))  # no refresh_token in the answer
    api.route("GET", "/invoices", lambda r: json_resp(INVOICES) if r.headers.get("authorization") == "Bearer tok" else json_resp({}, 401))
    conn = connection(api, auth={"type": "oauth2_refresh_token", "token_url": f"{api.base}/oauth/token"}, secrets={"client_id": "c", "client_secret": "s", "refresh_token": "rt-1"})
    assert len(fetch(conn)) == 2 and writes == [] and len(reads) == 1  # the store is read to get the current token; nothing is written

    reads.clear()
    rest_connector._TOKENS.clear()
    rest_connector._RECORDS.clear()
    cc = connection(api, auth={"type": "oauth2_client_credentials", "token_url": f"{api.base}/oauth/token"}, secrets={"client_id": "c", "client_secret": "s"})
    assert len(fetch(cc)) == 2 and writes == [] and reads == []  # there is no refresh token to rotate or to look up


def test_failing_to_store_a_rotated_token_never_fails_the_read_and_never_leaks_it(api, monkeypatch, caplog):
    import logging

    provider = RotatingProvider()
    api.route("POST", "/oauth/token", provider.token)
    api.route("GET", "/invoices", provider.data)

    class BrokenSession:
        def __init__(self, *a, **k):
            raise RuntimeError("the database is unreachable")

    monkeypatch.setattr("app.db.session.SessionLocal", BrokenSession)
    conn = connection(api, auth={"type": "oauth2_refresh_token", "token_url": f"{api.base}/oauth/token"}, secrets={"client_id": "c", "client_secret": "s", "refresh_token": "rt-1"})
    with caplog.at_level(logging.WARNING, logger="app.rest_connector"):
        assert len(fetch(conn)) == 2  # the data was read; only the bookkeeping failed
    assert "could not store a rotated refresh token" in caplog.text and "rt-2" not in caplog.text
