"""
Ready-made settings for well-known CRM APIs, built on the generic REST connector.

A preset is a template, not a separate connector: the user supplies a few facts (their instance address,
region, which objects they want) and the preset expands them into ordinary REST settings, which then go
through exactly the same validation and the same network policy as anything typed by hand. Nothing here
can name a host the user did not give; the only fixed addresses are the vendors' own token services.

HONEST LIMIT: these templates follow each vendor's public API documentation and are tested here against
local servers that answer in the documented shapes. They have NOT been run against a live Salesforce, Zoho
or Dynamics tenant (no credentials were available), so the first connection to a real one may need a
setting adjusted; every setting can be edited afterwards.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.connectors.config import ConfigError

_OBJECT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,60}$")
_ENTITY_SET = re.compile(r"^[a-z][a-z0-9_]{0,60}$")
_TENANT = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_API_VERSION = re.compile(r"^v\d{1,2}\.\d$")
_MAX_OBJECTS = 15


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    kind: str = "text"  # text | choice | list
    required: bool = True
    choices: tuple[str, ...] = ()
    default: object = None
    help: str = ""


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    description: str
    params: tuple[Param, ...]
    secrets: tuple[tuple[str, str], ...]  # (name, label) of what the user must supply, write-only
    field_order: tuple[str, ...] = field(default=())


def _choice(params: dict, name: str, allowed: tuple[str, ...], default: str) -> str:
    value = params.get(name, default)
    if value not in allowed:
        raise ConfigError(f"{name.replace('_', ' ').capitalize()} must be one of: {', '.join(allowed)}.")
    return value


def _names(params: dict, name: str, pattern: re.Pattern, default: list[str], label: str) -> list[str]:
    value = params.get(name) or default
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_OBJECTS or len(set(value)) != len(value):
        raise ConfigError(f"Choose between 1 and {_MAX_OBJECTS} distinct {label}.")
    for item in value:
        if not isinstance(item, str) or not pattern.match(item):
            raise ConfigError(f"'{str(item)[:40]}' is not a valid {label[:-1] if label.endswith('s') else label} name.")
    return value


def _url(params: dict, name: str, label: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} is required.")
    value = value.strip().rstrip("/")
    if not value.startswith("https://") or "?" in value or "#" in value or "/" in value[len("https://"):]:
        raise ConfigError(f"{label} must look like https://your-instance.example.com (no path).")
    return value  # the host itself is vetted by the REST validation this is passed through


def _version(params: dict, default: str) -> str:
    value = params.get("api_version", default)
    if not isinstance(value, str) or not _API_VERSION.match(value):
        raise ConfigError("The API version must look like v60.0.")
    return value


# --- Salesforce ------------------------------------------------------------------------------------------------
_SF_OBJECTS = ["Account", "Contact", "Opportunity", "Lead", "User"]


def _salesforce(params: dict) -> dict:
    instance = _url(params, "instance_url", "The Salesforce instance address")
    flow = _choice(params, "flow", ("refresh_token", "client_credentials"), "refresh_token")
    version = _version(params, "v60.0")
    objects = _names(params, "objects", _OBJECT, _SF_OBJECTS, "objects")
    if flow == "refresh_token":
        login = _choice(params, "login_url", ("https://login.salesforce.com", "https://test.salesforce.com"), "https://login.salesforce.com")
        auth = {"type": "oauth2_refresh_token", "token_url": f"{login}/services/oauth2/token"}
    else:
        auth = {"type": "oauth2_client_credentials", "token_url": f"{instance}/services/oauth2/token"}
    return {
        "base_url": instance, "auth": auth, "preset": "salesforce",
        "endpoints": [
            {
                "entity_name": obj, "path": f"/services/data/{version}/query", "query": {"q": f"SELECT FIELDS(STANDARD) FROM {obj}"},
                "records_path": "records", "pagination": {"type": "next_url", "next_url_path": "nextRecordsUrl", "max_pages": 10},
            }
            for obj in objects
        ],
    }


# --- Zoho CRM ----------------------------------------------------------------------------------------------------
_ZOHO_DATA_CENTERS = ("com", "eu", "in", "com.au", "jp", "ca", "sa", "com.cn")
# Zoho's list API requires an explicit field list. These are the standard fields of each module.
_ZOHO_MODULES = {
    "Leads": "id,First_Name,Last_Name,Email,Company,Lead_Status,Lead_Source,Owner,Created_Time,Modified_Time",
    "Contacts": "id,First_Name,Last_Name,Email,Account_Name,Owner,Created_Time,Modified_Time",
    "Accounts": "id,Account_Name,Website,Industry,Owner,Created_Time,Modified_Time",
    "Deals": "id,Deal_Name,Amount,Stage,Closing_Date,Account_Name,Owner,Created_Time,Modified_Time",
}


def _zoho(params: dict) -> dict:
    dc = _choice(params, "data_center", _ZOHO_DATA_CENTERS, "com")
    modules = _names(params, "modules", _OBJECT, list(_ZOHO_MODULES), "modules")
    unknown = [m for m in modules if m not in _ZOHO_MODULES]
    if unknown:
        raise ConfigError(f"The preset covers {', '.join(_ZOHO_MODULES)}. For other modules, add a REST connection by hand.")
    return {
        "base_url": f"https://www.zohoapis.{dc}/crm/v6",
        "auth": {"type": "oauth2_refresh_token", "token_url": f"https://accounts.zoho.{dc}/oauth/v2/token", "scheme": "Zoho-oauthtoken"},
        "preset": "zoho_crm",
        "endpoints": [
            {
                "entity_name": module, "path": f"/{module}", "query": {"fields": _ZOHO_MODULES[module]}, "records_path": "data",
                "pagination": {"type": "page", "page_param": "page", "size_param": "per_page", "page_size": 200, "max_pages": 10},
            }
            for module in modules
        ],
    }


# --- Microsoft Dynamics 365 CRM (Dataverse Web API) ------------------------------------------------------------------
_DYN_ENTITIES = ["accounts", "contacts", "opportunities", "leads", "systemusers"]


def _dynamics(params: dict) -> dict:
    environment = _url(params, "environment_url", "The Dynamics environment address")
    tenant = params.get("tenant_id")
    if not isinstance(tenant, str) or not _TENANT.match(tenant.strip()):
        raise ConfigError("The tenant id must be the directory (tenant) GUID from Azure.")
    version = _version(params, "v9.2")
    entities = _names(params, "entities", _ENTITY_SET, _DYN_ENTITIES, "tables")
    return {
        "base_url": f"{environment}/api/data/{version}",
        "auth": {
            "type": "oauth2_client_credentials", "token_url": f"https://login.microsoftonline.com/{tenant.strip()}/oauth2/v2.0/token",
            "scope": f"{environment}/.default",
        },
        "headers": {"OData-MaxVersion": "4.0", "OData-Version": "4.0", "Prefer": "odata.maxpagesize=500"},
        "preset": "dynamics_crm",
        "endpoints": [
            {
                "entity_name": entity, "path": f"/{entity}", "records_path": "value",
                "pagination": {"type": "next_url", "next_url_path": "@odata.nextLink", "max_pages": 10},
            }
            for entity in entities
        ],
    }


PRESETS: dict[str, tuple[Preset, object]] = {
    "salesforce": (
        Preset(
            "salesforce", "Salesforce", "Accounts, contacts, opportunities, leads and users (or any objects you name) from a Salesforce org.",
            (
                Param("instance_url", "Instance address", help="For example https://acme.my.salesforce.com"),
                Param("flow", "Sign-in method", "choice", choices=("refresh_token", "client_credentials"), default="refresh_token"),
                Param("login_url", "Login address (refresh-token sign-in)", "choice", False, ("https://login.salesforce.com", "https://test.salesforce.com"), "https://login.salesforce.com", "Use test.salesforce.com for a sandbox."),
                Param("objects", "Objects", "list", False, default=_SF_OBJECTS, help="API names, e.g. Account, Contact, Invoice__c"),
                Param("api_version", "API version", required=False, default="v60.0"),
            ),
            (("client_id", "Connected app consumer key"), ("client_secret", "Connected app consumer secret"), ("refresh_token", "Refresh token (refresh-token sign-in only)")),
        ),
        _salesforce,
    ),
    "zoho_crm": (
        Preset(
            "zoho_crm", "Zoho CRM", "Leads, contacts, accounts and deals from Zoho CRM.",
            (
                Param("data_center", "Data centre", "choice", False, _ZOHO_DATA_CENTERS, "com", "The region your Zoho account is in."),
                Param("modules", "Modules", "list", False, default=list(_ZOHO_MODULES), help=f"Any of {', '.join(_ZOHO_MODULES)}"),
            ),
            (("client_id", "Client id"), ("client_secret", "Client secret"), ("refresh_token", "Refresh token")),
        ),
        _zoho,
    ),
    "dynamics_crm": (
        Preset(
            "dynamics_crm", "Microsoft Dynamics 365 CRM", "Accounts, contacts, opportunities, leads and users from a Dynamics 365 (Dataverse) environment.",
            (
                Param("environment_url", "Environment address", help="For example https://acme.crm.dynamics.com"),
                Param("tenant_id", "Tenant id", help="The directory (tenant) GUID from Azure"),
                Param("entities", "Tables", "list", False, default=_DYN_ENTITIES, help="Dataverse entity-set names, e.g. accounts, contacts"),
                Param("api_version", "API version", required=False, default="v9.2"),
            ),
            (("client_id", "Application (client) id"), ("client_secret", "Client secret")),
        ),
        _dynamics,
    ),
}


def describe() -> list[dict]:
    """What the UI needs to draw each preset's form. No secrets, nothing about any connection."""
    return [
        {
            "key": preset.key, "label": preset.label, "description": preset.description,
            "params": [
                {"name": p.name, "label": p.label, "kind": p.kind, "required": p.required, "choices": list(p.choices), "default": p.default, "help": p.help}
                for p in preset.params
            ],
            "secrets": [{"name": n, "label": label} for n, label in preset.secrets],
        }
        for preset, _build in PRESETS.values()
    ]


def expand(key: str, params: dict) -> dict:
    """The REST settings a preset stands for. Raises ConfigError with a message fit to show."""
    if key not in PRESETS:
        raise ConfigError(f"Unknown preset '{key[:40]}'. Available: {', '.join(PRESETS)}.")
    if not isinstance(params, dict):
        raise ConfigError("The preset settings must be an object.")
    preset, build = PRESETS[key]
    allowed = {p.name for p in preset.params}
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ConfigError(f"{preset.label}: unknown setting(s) {', '.join(unknown)}.")
    return build(params)
