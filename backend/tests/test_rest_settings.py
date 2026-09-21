"""What a user may put in a REST / SOAP connection's settings. Pure: no database, no network.

Everything accepted here is later used to make requests from the platform's own network position, and the
settings are returned by the API, so the validation is an allow-list and refuses anything that could
name another host or carry a credential.
"""
import copy

import pytest

from app.services.connectors.config import ConfigError, prepare

GOOD = {
    "base_url": "https://api.example.com/v2",
    "auth": {"type": "bearer"},
    "endpoints": [{"entity_name": "invoices", "path": "/invoices", "records_path": "data.items"}],
}
SECRETS = {"token": "tok"}


def rest(mutate=None, secrets=None, kind="rest_api"):
    config = copy.deepcopy(GOOD)
    if mutate:
        mutate(config)
    return prepare(kind, config, SECRETS if secrets is None else secrets)


def refused(mutate=None, secrets=None, match=None, kind="rest_api"):
    with pytest.raises(ConfigError, match=match):
        rest(mutate, secrets, kind)


def test_a_valid_rest_connection_is_normalised_and_split():
    p = rest()
    assert p.host == "api.example.com" and p.port is None and p.username is None
    assert p.config["base_url"] == "https://api.example.com/v2" and p.config["endpoints"][0]["method"] == "GET"
    assert p.config["endpoints"][0]["pagination"] == {"type": "none"} and p.secrets == {"token": "tok"}
    assert "token" not in str(p.config)  # the secret is nowhere in what the API will return


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com", "ftp://api.example.com", "//api.example.com", "api.example.com", "https://", "https://user:pw@api.example.com",
        "https://api.example.com#frag", "https://api.example.com:notaport", "https://api.example.com/a b", "https://api.example.com\\x", "",
    ],
)
def test_the_base_url_must_be_a_plain_https_address(url):
    refused(lambda c: c.update(base_url=url))


@pytest.mark.parametrize("url", ["https://127.0.0.1", "https://10.0.0.5/api", "https://169.254.169.254/latest", "https://[::1]/", "https://192.168.0.1:8443"])
def test_an_internal_address_is_refused(url):
    refused(lambda c: c.update(base_url=url), match="public internet")


def test_a_public_literal_address_and_a_port_are_accepted():
    p = rest(lambda c: c.update(base_url="https://93.184.216.34:8443/api"))
    assert p.host == "93.184.216.34" and p.port == 8443


@pytest.mark.parametrize("path", ["invoices", "//evil.example/x", "https://evil.example/x", "/a?x=1", "/a#f", "/a b", "/a\\b", "http://x", ""])
def test_an_endpoint_path_is_relative_to_the_base_url_and_can_never_name_another_host(path):
    refused(lambda c: c["endpoints"][0].update(path=path))


def test_credentials_are_never_accepted_in_settings():
    for mutate in (
        lambda c: c.update(headers={"Authorization": "Bearer abc"}),
        lambda c: c.update(headers={"X-Api-Key": "abc"}),
        lambda c: c["endpoints"][0].update(query={"api_key": "abc"}),
        lambda c: c["endpoints"][0].update(query={"access_token": "abc"}),
        lambda c: c["endpoints"][0].update(method="POST", body={"auth": {"password": "hunter2"}}),
        lambda c: c["endpoints"][0].update(method="POST", body={"items": [{"client_secret": "x"}]}),
    ):
        refused(mutate, match="credential")


def test_a_credential_may_be_referred_to_by_a_placeholder_and_only_by_a_placeholder():
    ok = rest(
        lambda c: (c.update(headers={"X-Tenant": "{{secret.tenant}}", "X-Api-Key": "{{secret.key}}"}), c["endpoints"][0].update(query={"token": "{{secret.key}}"})),
        secrets={"token": "t", "tenant": "acme", "key": "k"},
    )
    assert ok.config["headers"]["X-Api-Key"] == "{{secret.key}}"
    labelled = rest(lambda c: c.update(headers={"Authorization": "Zoho-oauthtoken {{secret.key}}", "X-Auth-Token": "Token {{secret.key}}"}), secrets={"token": "t", "key": "k"})
    assert labelled.config["headers"]["Authorization"] == "Zoho-oauthtoken {{secret.key}}"  # a scheme label around the reference is fine
    for literal in ("{{secret.key}}abc123def456", "abc123", "sk_live_51H8xyz", "{{secret.key}}=="):
        refused(lambda c: c.update(headers={"X-Api-Key": literal}), secrets={"token": "t", "key": "k"}, match="credential")
    refused(lambda c: c.update(headers={"X-Tenant": "{{secret.nope}}"}), match="not supplied")


def test_the_framing_headers_belong_to_the_http_client():
    for name in ("Host", "content-length", "Transfer-Encoding", "Connection", "Upgrade", "Expect", "Proxy-Authorization"):
        refused(lambda c: c.update(headers={name: "x"}))
    refused(lambda c: c.update(headers={"bad name": "x"}))
    refused(lambda c: c.update(headers={"X-A": "line\nbreak"}))


def test_unknown_settings_are_refused_at_every_level():
    refused(lambda c: c.update(proxy="http://x"), match="unknown")
    refused(lambda c: c["endpoints"][0].update(verify_tls=False), match="unknown")
    refused(lambda c: c.update(auth={"type": "bearer", "extra": 1}), match="unknown")
    refused(lambda c: c["endpoints"][0].update(pagination={"type": "page", "surprise": 1}), match="unknown")


@pytest.mark.parametrize("count", [0, 21])
def test_the_number_of_endpoints_is_bounded(count):
    refused(lambda c: c.update(endpoints=[{"entity_name": f"t{i}", "path": "/x"} for i in range(count)]), match="between 1 and 20")


def test_table_names_must_be_unique_and_present():
    refused(lambda c: c["endpoints"].append({"entity_name": "INVOICES", "path": "/x"}), match="same|Two endpoints")
    refused(lambda c: c["endpoints"][0].update(entity_name=""), match="required")


def test_methods_and_bodies():
    assert rest(lambda c: c["endpoints"][0].update(method="post", body={"a": 1})).config["endpoints"][0]["method"] == "POST"
    refused(lambda c: c["endpoints"][0].update(method="DELETE"))
    refused(lambda c: c["endpoints"][0].update(method="PUT"))
    refused(lambda c: c["endpoints"][0].update(method="GET", body={"a": 1}), match="POST")
    refused(lambda c: c["endpoints"][0].update(method="POST", body="x" * 20_001))


@pytest.mark.parametrize("path", ["a..b", "a b", "a/b", ".a", "a.", "x" * 201])
def test_a_records_path_is_a_dotted_path(path):
    refused(lambda c: c["endpoints"][0].update(records_path=path), match="dotted path")


@pytest.mark.parametrize(
    "pagination, match",
    [
        ({"type": "nonsense"}, "pagination type"),
        ({"type": "page", "max_pages": 0}, "max_pages"),
        ({"type": "page", "max_pages": 51}, "max_pages"),
        ({"type": "page", "page_size": 0}, "page_size"),
        ({"type": "page", "page_size": 1001}, "page_size"),
        ({"type": "page", "page_param": "a b"}, "parameter name"),
        ({"type": "cursor"}, "required"),
        ({"type": "cursor", "cursor_param": "cursor"}, "required"),
        ({"type": "next_url"}, "required"),
        ({"type": "offset", "max_pages": True}, "max_pages"),
    ],
)
def test_pagination_settings_are_validated_and_bounded(pagination, match):
    refused(lambda c: c["endpoints"][0].update(pagination=pagination), match=match)


def test_every_pagination_type_is_accepted_with_sensible_defaults():
    for pagination in (
        {"type": "page"}, {"type": "offset"}, {"type": "link_header"},
        {"type": "cursor", "cursor_param": "after", "next_cursor_path": "meta.next"}, {"type": "next_url", "next_url_path": "links.next"},
    ):
        got = rest(lambda c: c["endpoints"][0].update(pagination=pagination)).config["endpoints"][0]["pagination"]
        assert got["type"] == pagination["type"] and got["max_pages"] == 10


@pytest.mark.parametrize(
    "auth, secrets, match",
    [
        ({"type": "bearer"}, {}, "token"),
        ({"type": "basic", "username": "u"}, {}, "password"),
        ({"type": "basic"}, {"password": "p"}, "required"),
        ({"type": "api_key", "in": "header", "name": "X-Key"}, {}, "api_key"),
        ({"type": "api_key", "in": "cookie", "name": "X"}, {"api_key": "k"}, "header' or the 'query"),
        ({"type": "api_key", "in": "header", "name": "Host"}, {"api_key": "k"}, "valid header"),
        ({"type": "oauth2_client_credentials", "token_url": "https://auth.example.com/token"}, {"client_id": "i"}, "client_secret"),
        ({"type": "oauth2_client_credentials", "token_url": "http://auth.example.com/token"}, {"client_id": "i", "client_secret": "s"}, "https"),
        ({"type": "oauth2_client_credentials", "token_url": "https://10.0.0.1/token"}, {"client_id": "i", "client_secret": "s"}, "public internet"),
        ({"type": "oauth2_refresh_token", "token_url": "https://auth.example.com/token"}, {"client_id": "i", "client_secret": "s"}, "refresh_token"),
        ({"type": "magic"}, {}, "type must be"),
    ],
)
def test_authentication_needs_its_settings_and_its_secrets(auth, secrets, match):
    refused(lambda c: c.update(auth=auth), secrets=secrets, match=match)


def test_each_authentication_type_is_accepted_when_complete():
    for auth, secrets in (
        ({"type": "none"}, {}),
        ({"type": "api_key", "in": "query", "name": "key"}, {"api_key": "k"}),
        ({"type": "basic", "username": "u"}, {"password": "p"}),
        ({"type": "oauth2_client_credentials", "token_url": "https://auth.example.com/token", "scope": "read"}, {"client_id": "i", "client_secret": "s"}),
        ({"type": "oauth2_refresh_token", "token_url": "https://auth.example.com/token"}, {"client_id": "i", "client_secret": "s", "refresh_token": "r"}),
    ):
        assert rest(lambda c: c.update(auth=auth), secrets=secrets).config["auth"]["type"] == auth["type"]


def test_secret_names_and_sizes_are_bounded():
    refused(secrets={"token": "t", "Bad Name": "x"}, match="Secret names")
    refused(secrets={"token": "t", **{f"s{i}": "x" for i in range(12)}}, match="Secret names")
    refused(secrets={"token": "x" * 16_001}, match="too long")


def test_a_soap_endpoint_needs_an_envelope_and_only_posts():
    soap = {
        "base_url": "https://soap.example.com", "auth": {"type": "none"},
        "endpoints": [{"entity_name": "invoices", "path": "/svc", "body": "<Envelope/>", "soap_action": "urn:GetInvoices", "records_path": "Envelope.Body.R.Invoice"}],
    }
    p = prepare("soap_api", soap, {})
    assert p.config["endpoints"][0]["method"] == "POST" and p.config["endpoints"][0]["soap_action"] == "urn:GetInvoices"
    for bad in (
        lambda c: c["endpoints"][0].pop("body"),
        lambda c: c["endpoints"][0].update(body=""),
        lambda c: c["endpoints"][0].update(body={"json": "not xml text"}),
        lambda c: c["endpoints"][0].update(method="GET"),
        lambda c: c["endpoints"][0].update(soap_action="a\nb"),
    ):
        broken = copy.deepcopy(soap)
        bad(broken)
        with pytest.raises(ConfigError):
            prepare("soap_api", broken, {})


def test_the_preset_name_is_only_a_label():
    assert rest(lambda c: c.update(preset="salesforce")).config["preset"] == "salesforce"
    refused(lambda c: c.update(preset="Sales Force!"), match="preset")


def test_oversized_settings_are_refused():
    refused(lambda c: c.update(headers={"X-A": "y" * 501}))
    refused(lambda c: c["endpoints"][0].update(query={f"p{i}": "v" for i in range(21)}))


def test_the_authorization_scheme_is_a_single_word():
    assert rest(lambda c: c.update(auth={"type": "bearer", "scheme": "Zoho-oauthtoken"})).config["auth"]["scheme"] == "Zoho-oauthtoken"
    for bad in ("Two Words", "", "Bear\ner", "9Bearer", "x" * 40):
        refused(lambda c: c.update(auth={"type": "bearer", "scheme": bad}), match="scheme")


# --- hardening found on review: control characters in secrets, credentials pasted into a text body ---------------------------
@pytest.mark.parametrize("bad", ["line\nbreak", "carriage\rreturn", "nul\x00byte", "X-Evil: 1\r\nInjected: yes"])
def test_a_secret_with_a_line_break_or_control_character_is_refused_because_it_would_be_header_injection(bad):
    refused(lambda c: c.update(headers={"X-Tenant": "{{secret.tenant}}"}), secrets={"token": "t", "tenant": bad}, match="line break")
    refused(secrets={"token": bad}, match="line break")


SOAP_BASE = {
    "base_url": "https://soap.example.com", "auth": {"type": "none"},
    "endpoints": [{"entity_name": "invoices", "path": "/svc", "body": "<Envelope/>", "soap_action": "urn:Get"}],
}


def soap_with_body(body, secrets=None):
    config = copy.deepcopy(SOAP_BASE)
    config["endpoints"][0]["body"] = body
    return prepare("soap_api", config, secrets or {})


@pytest.mark.parametrize(
    "body",
    [
        "<Envelope><Body><Login><Password>hunter2</Password></Login></Body></Envelope>",
        '<wsse:UsernameToken><wsse:Password Type="PasswordText">s3cr3t-value</wsse:Password></wsse:UsernameToken>',
        "<Auth><ApiKey>abcd1234efgh</ApiKey></Auth>",
        "<Auth><ClientSecret>abcd1234</ClientSecret></Auth>",
        "<x><Token>eyJhbGciOi</Token></x>",
    ],
)
def test_a_credential_written_into_a_soap_envelope_is_refused(body):
    with pytest.raises(ConfigError, match="credential written into the text"):
        soap_with_body(body)


def test_an_envelope_may_refer_to_a_secret_or_carry_ordinary_data():
    ok = soap_with_body(
        "<Envelope><Body><Get><Account>ACC-42</Account><Password>{{secret.pw}}</Password><Empty></Empty></Get></Body></Envelope>",
        secrets={"pw": "the-real-one"},
    )
    assert "the-real-one" not in str(ok.config)  # only the reference is stored
    assert ok.secrets == {"pw": "the-real-one"}
    soap_with_body("<Get><Account>ACC-42</Account><Period>2026-09</Period></Get>")  # nothing credential-like: accepted


@pytest.mark.parametrize(
    "body",
    ['{"password": "hunter2"}', '{"client_secret":"abcdef"}', "grant_type=x&password=hunter2", "api_key=abcd1234", "token: eyJhbGci"],
)
def test_a_credential_written_into_a_text_or_json_string_body_is_refused(body):
    with pytest.raises(ConfigError, match="credential"):
        rest(lambda c: c["endpoints"][0].update(method="POST", body=body))


def test_a_text_body_that_refers_to_a_secret_is_accepted():
    p = rest(lambda c: c["endpoints"][0].update(method="POST", body="grant_type=x&password={{secret.pw}}"), secrets={"token": "t", "pw": "p"})
    assert p.config["endpoints"][0]["body"] == "grant_type=x&password={{secret.pw}}"
