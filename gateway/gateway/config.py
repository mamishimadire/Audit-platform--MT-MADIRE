import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from gateway.connectors.base import ConnectorConfig


@dataclass
class ConnectionEntry:
    connection_id: str
    source_type: str
    connector_config: ConnectorConfig
    # Per-connection opt-out of column profiling (see gateway/profiling.py).
    profiling: bool = True
    # Per-connection opt-out of relationship measurement (see gateway/relationships.py).
    relationships: bool = True


@dataclass
class GatewaySettings:
    platform_url: str
    heartbeat_interval_seconds: int
    connections: list[ConnectionEntry]
    # Column profiling: a small summary of what each discovered column holds
    # (never sensitive values), refreshed at most this often.
    profiling_enabled: bool = True
    profile_interval_hours: int = 24
    profile_sample_rows: int = 500
    # Relationship measurement: counts only, never values. Off entirely with false.
    relationships_enabled: bool = True


def _resolve_password(raw: dict) -> str:
    if "password_env" in raw:
        env_name = raw["password_env"]
        value = os.environ.get(env_name)
        if not value:
            raise RuntimeError(f"Environment variable '{env_name}' is not set (referenced by password_env)")
        return value
    if "password" in raw:
        return raw["password"]
    raise RuntimeError("Connection config needs either 'password' or 'password_env'")


def _need(entry: dict, key: str, *, why: str = "") -> object:
    value = entry.get(key)
    if value in (None, ""):
        raise RuntimeError(f"Connection '{entry.get('connection_id', '?')}' ({entry.get('type', '?')}) needs '{key}'{f' — {why}' if why else ''}")
    return value


def _snowflake_private_key(entry: dict) -> str:
    """The PEM private key for key-pair sign-in, read from a file or an environment variable — never written
    into config.yaml itself, so the file that gets emailed around when someone asks for help holds no key."""
    if "private_key_file" in entry:
        path = Path(str(entry["private_key_file"])).expanduser()
        if not path.is_file():
            raise RuntimeError(f"private_key_file '{path}' does not exist (connection '{entry.get('connection_id', '?')}')")
        return path.read_text()
    if "private_key_env" in entry:
        value = os.environ.get(entry["private_key_env"])
        if not value:
            raise RuntimeError(f"Environment variable '{entry['private_key_env']}' is not set (referenced by private_key_env)")
        return value
    raise RuntimeError(
        f"Connection '{entry.get('connection_id', '?')}' (snowflake) uses key_pair sign-in and needs 'private_key_file' or 'private_key_env'"
    )


def _connector_config(entry: dict) -> ConnectorConfig:
    source_type = entry["type"]
    if source_type == "snowflake":
        method = entry.get("snowflake_auth_method", "password")
        if method not in ("password", "key_pair"):
            raise RuntimeError(f"snowflake_auth_method must be 'password' or 'key_pair', not '{method}'")
        passphrase_env = entry.get("private_key_passphrase_env")
        if passphrase_env and not os.environ.get(passphrase_env):
            raise RuntimeError(f"Environment variable '{passphrase_env}' is not set (referenced by private_key_passphrase_env)")
        return ConnectorConfig(
            host=str(_need(entry, "host", why="the Snowflake account identifier, e.g. xy12345.eu-west-1")),
            port=int(entry.get("port", 443)),
            database=str(_need(entry, "database")),
            username=str(_need(entry, "username")),
            password=_resolve_password(entry) if method == "password" else "",
            schema=str(_need(entry, "schema")),
            snowflake_warehouse=str(_need(entry, "snowflake_warehouse")),
            snowflake_role=entry.get("snowflake_role"),
            snowflake_auth_method=method,
            snowflake_private_key=_snowflake_private_key(entry) if method == "key_pair" else None,
            snowflake_key_passphrase=os.environ.get(passphrase_env) if passphrase_env else None,
        )
    if source_type == "sap_hana":
        return ConnectorConfig(
            host=str(_need(entry, "host")), port=int(_need(entry, "port")),
            database=str(entry.get("database") or ""),  # a tenant-database connection needs only host and port
            username=str(_need(entry, "username")), password=_resolve_password(entry), schema=entry.get("schema"),
            sap_hana_encrypt=bool(entry.get("sap_hana_encrypt", True)),
        )
    if source_type == "oracle":
        kind = entry.get("oracle_connection_type", "service_name")
        if kind not in ("service_name", "sid"):
            raise RuntimeError(f"oracle_connection_type must be 'service_name' or 'sid', not '{kind}'")
        return ConnectorConfig(
            host=str(_need(entry, "host")), port=int(entry.get("port", 1521)),
            database=str(_need(entry, "database", why="the Oracle service name (or the SID, with oracle_connection_type: sid)")),
            username=str(_need(entry, "username")), password=_resolve_password(entry), schema=entry.get("schema"),
            oracle_connection_type=kind,
        )
    return ConnectorConfig(
        host=entry["host"],
        port=int(entry["port"]),
        database=entry["database"],
        username=entry["username"],
        password=_resolve_password(entry),
        schema=entry.get("schema"),
        mongodb_srv=bool(entry.get("mongodb_srv", False)),
    )


def load_settings(path: Path) -> GatewaySettings:
    raw = yaml.safe_load(path.read_text())
    connections = []
    for entry in raw.get("connections", []):
        connections.append(
            ConnectionEntry(
                connection_id=entry["connection_id"],
                source_type=entry["type"],
                connector_config=_connector_config(entry),
                profiling=bool(entry.get("profiling", True)),
                relationships=bool(entry.get("relationships", True)),
            )
        )
    return GatewaySettings(
        platform_url=raw["platform_url"],
        heartbeat_interval_seconds=int(raw.get("heartbeat_interval_seconds", 300)),
        connections=connections,
        profiling_enabled=bool(raw.get("profiling_enabled", True)),
        profile_interval_hours=max(1, int(raw.get("profile_interval_hours", 24))),
        profile_sample_rows=max(50, min(int(raw.get("profile_sample_rows", 500)), 5000)),
        relationships_enabled=bool(raw.get("relationships_enabled", True)),
    )
