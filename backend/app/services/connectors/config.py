"""
What each non-database connection family accepts, and where each part is stored.

  host / port / username   the existing DataConnection columns, so lists and audit entries can show them
  connector_config         every other setting that is NOT secret. It is returned by the API, so a
                           credential must never be in it — refused here, not merely discouraged
  encrypted_password       ONE encrypted JSON object holding every secret of the connection (SFTP
                           password / private key, later API keys, tokens, client secrets)

Everything a user typed is validated against an allow-list per family: an unknown setting is refused
rather than stored, so nothing surprising ends up in config, the audit log or a response.
"""
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.net_guard import is_public_ip

MAX_CONFIG_BYTES = 20_000
MAX_SECRET_CHARS = 16_000  # a 4096-bit RSA private key in PEM is about 3.3k characters

_HOSTNAME = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
_FINGERPRINT = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")  # exactly what `ssh-keygen -lf` prints


@dataclass
class Prepared:
    """A validated connection, split into the columns it is stored in."""

    config: dict | None
    secrets: dict[str, str]
    host: str | None = None
    port: int | None = None
    username: str | None = None
    notes: list[str] = field(default_factory=list)


class ConfigError(ValueError):
    """A setting is missing or not acceptable. The message is written to be shown to the user."""


def pack_secrets(secrets: dict[str, str]) -> str | None:
    return encrypt_secret(json.dumps(secrets, separators=(",", ":"))) if secrets else None


def unpack_secrets(encrypted: str | None) -> dict[str, str]:
    if not encrypted:
        return {}
    loaded = json.loads(decrypt_secret(encrypted))
    return {str(k): str(v) for k, v in loaded.items()} if isinstance(loaded, dict) else {}


def _only_keys(what: str, given: dict, allowed: set[str]) -> None:
    unknown = sorted(set(given) - allowed)
    if unknown:
        raise ConfigError(f"{what}: unknown setting(s) {', '.join(unknown)}.")


def _text(config: dict, key: str, *, label: str, required: bool = True, max_len: int = 500) -> str | None:
    value = config.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ConfigError(f"{label} is required.")
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{label} must be text.")
    value = value.strip()
    if len(value) > max_len or "\x00" in value or "\n" in value or "\r" in value:
        raise ConfigError(f"{label} is not valid.")
    return value


def check_public_host(host: str) -> str:
    """Syntax and literal-address check for a server the platform will dial itself. Names are resolved
    (and every address vetted) only when a connection is actually attempted, by net_guard."""
    host = host.strip().lower().rstrip(".")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        if not _HOSTNAME.match(host):
            raise ConfigError("The host must be a server name or IP address, without a scheme, port or path.") from None
        return host
    if not is_public_ip(literal):
        raise ConfigError(
            "That address is not reachable from the public internet. The platform only connects to public servers; "
            "reach an internal server through a Gateway."
        )
    return str(literal)


def refuse_internal_literal(host: str | None) -> None:
    """For the database connections, whose host names have always been accepted as typed: refuse ONLY an
    address literal that is not public (127.0.0.1, 10.x, 169.254.169.254 ...), with the same message. Names are
    vetted, address by address, when the connection is actually used."""
    if not host:
        return
    try:
        literal = ipaddress.ip_address(host.strip().strip("[]"))
    except ValueError:
        return
    if not is_public_ip(literal):
        raise ConfigError(
            "That address is not reachable from the public internet. The platform only connects to public servers; "
            "reach an internal server through a Gateway."
        )


def _prepare_file_upload(config: dict, secrets: dict[str, str]) -> Prepared:
    if config or secrets:
        raise ConfigError("A file connection takes no settings — upload files to it after it is created.")
    return Prepared(config=None, secrets={})


def _prepare_sftp(config: dict, secrets: dict[str, str]) -> Prepared:
    _only_keys("SFTP", config, {"host", "port", "username", "remote_path", "table_name", "auth", "host_key_sha256"})
    host = check_public_host(_text(config, "host", label="Host", max_len=253) or "")
    port = config.get("port", 22)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConfigError("Port must be a number from 1 to 65535.")
    username = _text(config, "username", label="Username", max_len=150)
    remote_path = _text(config, "remote_path", label="Remote path")
    auth = config.get("auth", "password")
    if auth not in ("password", "private_key"):
        raise ConfigError("Authentication must be 'password' or 'private_key'.")
    pinned = config.get("host_key_sha256")
    if pinned is not None and not (isinstance(pinned, str) and _FINGERPRINT.match(pinned)):
        raise ConfigError("The host key fingerprint must look like SHA256:… (as printed by ssh-keygen -lf).")

    _only_keys("SFTP secrets", secrets, {"password", "private_key", "passphrase"})
    if auth == "password":
        if not secrets.get("password"):
            raise ConfigError("A password is required for password authentication.")
        if secrets.get("private_key") or secrets.get("passphrase"):
            raise ConfigError("A private key was supplied but the authentication is set to password.")
    else:
        key = secrets.get("private_key", "")
        if "PRIVATE KEY" not in key:
            raise ConfigError("A PEM private key is required for key authentication.")
        if secrets.get("password"):
            raise ConfigError("A password was supplied but the authentication is set to private_key.")
    for name, value in secrets.items():
        if len(value) > MAX_SECRET_CHARS:
            raise ConfigError(f"The {name.replace('_', ' ')} is too long.")

    cleaned = {"remote_path": remote_path, "auth": auth}
    table_name = _text(config, "table_name", label="Table name", required=False, max_len=120)
    if table_name:
        cleaned["table_name"] = table_name
    if pinned:
        cleaned["host_key_sha256"] = pinned
    return Prepared(config=cleaned, secrets={k: v for k, v in secrets.items() if v}, host=host, port=port, username=username)


# The settings a family stores in the DataConnection columns rather than in connector_config: an edit
# request passes them back through `prepare` together with the rest, so they are validated as one whole.
COLUMN_SETTINGS = {"sftp": ("host", "port", "username")}

_PREPARERS = {"file_upload": _prepare_file_upload, "sftp": _prepare_sftp}


def register_preparer(db_type: str, preparer) -> None:
    """A family's validation, registered by its own module (kept out of this file to keep it readable)."""
    _PREPARERS[db_type] = preparer


def prepare(db_type: str, config: dict, secrets: dict[str, str]) -> Prepared:
    if len(json.dumps(config, default=str)) > MAX_CONFIG_BYTES:
        raise ConfigError("The settings are too large.")
    preparer = _PREPARERS.get(db_type)
    if preparer is None:
        raise ConfigError(f"{db_type} connections are not available yet.")
    return preparer(config, secrets)
