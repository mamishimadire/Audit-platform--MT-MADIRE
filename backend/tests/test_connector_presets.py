"""The Salesforce / Zoho CRM / Dynamics 365 CRM templates: what each expands to, and that nothing a user types can
steer them to a host they did not name. Pure: no database, no network. (The vendor-shaped mock servers that
exercise the expanded settings live in test_rest_connector.py.)"""
import json

import pytest

from app.services.connectors import config as connector_settings, presets
from app.services.connectors.config import ConfigError, prepare

SF_SECRETS = {"client_id": "cid", "client_secret": "csecret", "refresh_token": "rtoken"}
TENANT = "0f3b2a1c-9d8e-4f7a-b6c5-123456789abc"


def build(key, params, secrets):
    return prepare("rest_api", presets.expand(key, params), secrets)


def test_the_three_presets_are_listed_for_the_ui_without_any_secret_value():
    listed = presets.describe()
    assert [p["key"] for p in listed] == ["salesforce", "zoho_crm", "dynamics_crm"]
    for p in listed:
        assert p["label"] and p["description"] and p["secrets"] and all({"name", "label", "kind", "required"} <= set(param) for param in p["params"])
    assert "client_secret" in json.dumps(listed)  # the NAME of what to ask for...
    assert "csecret" not in json.dumps(listed)


def test_salesforce_expands_to_a_refresh_token_connection_with_one_query_endpoint_per_object():
    p = build("salesforce", {"instance_url": "https://acme.my.salesforce.com/"}, SF_SECRETS)
    c = p.config
    assert p.host == "acme.my.salesforce.com" and c["base_url"] == "https://acme.my.salesforce.com" and c["preset"] == "salesforce"
    assert c["auth"] == {"type": "oauth2_refresh_token", "token_url": "https://login.salesforce.com/services/oauth2/token"}
    assert [e["entity_name"] for e in c["endpoints"]] == ["Account", "Contact", "Opportunity", "Lead", "User"]
    account = c["endpoints"][0]
    assert account["path"] == "/services/data/v60.0/query" and account["query"] == {"q": "SELECT FIELDS(STANDARD) FROM Account"}
    assert account["records_path"] == "records" and account["pagination"]["type"] == "next_url" and account["pagination"]["next_url_path"] == "nextRecordsUrl"
    assert not any(v in json.dumps(c) for v in SF_SECRETS.values())


def test_salesforce_sandbox_custom_objects_and_the_client_credentials_flow():
    sandbox = presets.expand("salesforce", {"instance_url": "https://acme--dev.sandbox.my.salesforce.com", "login_url": "https://test.salesforce.com", "objects": ["Invoice__c"]})
    assert sandbox["auth"]["token_url"] == "https://test.salesforce.com/services/oauth2/token" and sandbox["endpoints"][0]["query"]["q"].endswith("FROM Invoice__c")
    cc = presets.expand("salesforce", {"instance_url": "https://acme.my.salesforce.com", "flow": "client_credentials", "objects": "Account, Contact"})
    assert cc["auth"] == {"type": "oauth2_client_credentials", "token_url": "https://acme.my.salesforce.com/services/oauth2/token"}
    assert [e["entity_name"] for e in cc["endpoints"]] == ["Account", "Contact"]  # a comma-separated list is accepted too
    prepare("rest_api", cc, {"client_id": "a", "client_secret": "b"})  # client credentials needs no refresh token
    with pytest.raises(ConfigError, match="refresh_token"):
        prepare("rest_api", presets.expand("salesforce", {"instance_url": "https://acme.my.salesforce.com"}), {"client_id": "a", "client_secret": "b"})


def test_zoho_uses_its_regional_addresses_its_own_token_scheme_and_explicit_field_lists():
    p = build("zoho_crm", {"data_center": "eu", "modules": ["Leads", "Deals"]}, {"client_id": "a", "client_secret": "b", "refresh_token": "c"})
    c = p.config
    assert p.host == "www.zohoapis.eu" and c["base_url"] == "https://www.zohoapis.eu/crm/v6"
    assert c["auth"] == {"type": "oauth2_refresh_token", "token_url": "https://accounts.zoho.eu/oauth/v2/token", "scheme": "Zoho-oauthtoken"}
    leads, deals = c["endpoints"]
    assert leads["path"] == "/Leads" and "Lead_Status" in leads["query"]["fields"] and "Amount" in deals["query"]["fields"]
    assert leads["records_path"] == "data" and leads["pagination"] == {"type": "page", "page_param": "page", "size_param": "per_page", "page_size": 200, "max_pages": 10, "start": 1}
    assert presets.expand("zoho_crm", {})["base_url"] == "https://www.zohoapis.com/crm/v6"  # defaults: .com, all four modules


def test_dynamics_expands_to_client_credentials_against_the_tenants_token_service():
    p = build("dynamics_crm", {"environment_url": "https://acme.crm4.dynamics.com", "tenant_id": TENANT}, {"client_id": "a", "client_secret": "b"})
    c = p.config
    assert p.host == "acme.crm4.dynamics.com" and c["base_url"] == "https://acme.crm4.dynamics.com/api/data/v9.2"
    assert c["auth"]["token_url"] == f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token" and c["auth"]["scope"] == "https://acme.crm4.dynamics.com/.default"
    assert c["headers"]["Prefer"] == "odata.maxpagesize=500"
    assert [e["entity_name"] for e in c["endpoints"]] == ["accounts", "contacts", "opportunities", "leads", "systemusers"]
    assert c["endpoints"][0]["pagination"]["next_url_path"] == "@odata.nextLink" and c["endpoints"][0]["records_path"] == "value"


@pytest.mark.parametrize(
    "key, params",
    [
        ("salesforce", {"instance_url": "http://acme.my.salesforce.com"}),
        ("salesforce", {"instance_url": "https://acme.my.salesforce.com/some/path"}),
        ("salesforce", {"instance_url": "https://acme.my.salesforce.com?x=1"}),
        ("salesforce", {"instance_url": "acme.my.salesforce.com"}),
        ("salesforce", {"instance_url": ""}),
        ("salesforce", {}),
        ("salesforce", {"instance_url": "https://acme.my.salesforce.com", "flow": "password"}),
        ("salesforce", {"instance_url": "https://acme.my.salesforce.com", "login_url": "https://evil.example.com"}),
        ("salesforce", {"instance_url": "https://acme.my.salesforce.com", "api_version": "v60"}),
        ("salesforce", {"instance_url": "https://acme.my.salesforce.com", "api_version": "60.0/../../x"}),
        ("salesforce", {"instance_url": "https://acme.my.salesforce.com", "unexpected": 1}),
        ("zoho_crm", {"data_center": "evil.example"}),
        ("zoho_crm", {"data_center": "com/../x"}),
        ("zoho_crm", {"modules": ["Leads", "Invoices"]}),
        ("dynamics_crm", {"environment_url": "https://acme.crm.dynamics.com"}),
        ("dynamics_crm", {"environment_url": "https://acme.crm.dynamics.com", "tenant_id": "not-a-guid"}),
        ("dynamics_crm", {"environment_url": "https://acme.crm.dynamics.com", "tenant_id": TENANT + "/../x"}),
        ("dynamics_crm", {"environment_url": "https://acme.crm.dynamics.com", "tenant_id": TENANT, "entities": ["Accounts"]}),
        ("nonexistent", {}),
    ],
)
def test_a_preset_refuses_anything_that_could_steer_it_elsewhere(key, params):
    with pytest.raises(ConfigError):
        presets.expand(key, params)


@pytest.mark.parametrize("bad", ["Account WHERE Name = 'x'", "Account; DROP", "Account,Contact FROM", "1Account", "Acc ount", "Account'", "A" * 70, "Account)--", ""])
def test_an_object_name_cannot_smuggle_query_text(bad):
    with pytest.raises(ConfigError):
        presets.expand("salesforce", {"instance_url": "https://acme.my.salesforce.com", "objects": [bad]})


def test_the_number_and_uniqueness_of_objects_are_bounded():
    with pytest.raises(ConfigError):
        presets.expand("salesforce", {"instance_url": "https://acme.my.salesforce.com", "objects": [f"Obj{i}__c" for i in range(16)]})
    with pytest.raises(ConfigError):
        presets.expand("salesforce", {"instance_url": "https://acme.my.salesforce.com", "objects": ["Account", "Account"]})


@pytest.mark.parametrize("url", ["https://10.0.0.5", "https://127.0.0.1", "https://169.254.169.254", "https://[::1]"])
def test_a_preset_pointed_at_an_internal_address_is_refused_by_the_common_validation(url):
    with pytest.raises(ConfigError, match="public internet"):
        prepare("rest_api", presets.expand("salesforce", {"instance_url": url}), SF_SECRETS)


def test_no_expansion_carries_a_credential_or_a_host_the_user_did_not_give():
    for key, params in (
        ("salesforce", {"instance_url": "https://acme.my.salesforce.com"}),
        ("zoho_crm", {"data_center": "in"}),
        ("dynamics_crm", {"environment_url": "https://acme.crm.dynamics.com", "tenant_id": TENANT}),
    ):
        config = presets.expand(key, params)
        hosts = {config["base_url"].split("/")[2], config["auth"]["token_url"].split("/")[2]}
        assert hosts <= {"acme.my.salesforce.com", "login.salesforce.com", "www.zohoapis.in", "accounts.zoho.in", "acme.crm.dynamics.com", "login.microsoftonline.com"}
        assert "secret" not in json.dumps(config).replace("client_secret", "")  # nothing credential-like beyond names


def test_the_config_of_a_preset_is_stored_and_returned_like_any_other_and_holds_no_secrets():
    p = build("salesforce", {"instance_url": "https://acme.my.salesforce.com"}, SF_SECRETS)
    blob = connector_settings.pack_secrets(p.secrets)
    assert all(v not in json.dumps(p.config) for v in SF_SECRETS.values()) and all(v not in blob for v in SF_SECRETS.values())
