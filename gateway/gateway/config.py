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
                    mongodb_srv=bool(entry.get("mongodb_srv", False)),
                ),
                profiling=bool(entry.get("profiling", True)),
            )
        )
    return GatewaySettings(
        platform_url=raw["platform_url"],
        heartbeat_interval_seconds=int(raw.get("heartbeat_interval_seconds", 300)),
        connections=connections,
        profiling_enabled=bool(raw.get("profiling_enabled", True)),
        profile_interval_hours=max(1, int(raw.get("profile_interval_hours", 24))),
        profile_sample_rows=max(50, min(int(raw.get("profile_sample_rows", 500)), 5000)),
    )
