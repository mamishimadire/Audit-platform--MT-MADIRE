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


@dataclass
class GatewaySettings:
    platform_url: str
    heartbeat_interval_seconds: int
    connections: list[ConnectionEntry]


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


def load_settings(path: Path) -> GatewaySettings:
    raw = yaml.safe_load(path.read_text())
    connections = []
    for entry in raw.get("connections", []):
        connections.append(
            ConnectionEntry(
                connection_id=entry["connection_id"],
                source_type=entry["type"],
                connector_config=ConnectorConfig(
                    host=entry["host"],
                    port=int(entry["port"]),
                    database=entry["database"],
                    username=entry["username"],
                    password=_resolve_password(entry),
                    schema=entry.get("schema"),
                ),
            )
        )
    return GatewaySettings(
        platform_url=raw["platform_url"],
        heartbeat_interval_seconds=int(raw.get("heartbeat_interval_seconds", 300)),
        connections=connections,
    )
