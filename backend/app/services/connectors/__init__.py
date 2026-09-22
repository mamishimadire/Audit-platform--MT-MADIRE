"""
Connection families that are not databases: uploaded files, SFTP drops, REST/SOAP APIs.

A connector supplies exactly three things and gets everything else the platform does for a database
connection: discovery into the catalogue, field mapping, value profiling, relationship checks, and
scheduled test execution (which only ever asks a direct connection for `list[dict]` rows).

    test(connection)                 -> (ok, plain-language detail)
    discover(connection)             -> list[DiscoveredEntity]
    fetch_records(connection, entity_name=..., field_names=[...], limit=N) -> list[dict]

and optionally

    truncated(connection, entity_name) -> bool     # more rows exist than are read; a measurement over them is a lower bound

They register by `db_type`. The database engines keep their existing SQLAlchemy/Mongo paths; every
dispatch point in data_source_service asks this registry first. A connector must never raise raw
driver/network text at the user (it can echo credentials or addresses): raise ConnectorError with a
message that is safe to show.
"""
from __future__ import annotations

from typing import Protocol

from app.models.data_source import DataConnection
from app.schemas.data_source import DiscoveredEntity

# db_type values owned by this package (a connection of one of these is mode='direct').
CONNECTOR_TYPES = ("file_upload", "sftp", "rest_api", "soap_api")


class ConnectorError(Exception):
    """A failure whose message is safe to show the user."""


class EntityNotHere(ConnectorError):
    """The table asked for is not one this connection supplies (another connection of the same data
    source may). Distinct from a failure to read a table it does supply."""


class Connector(Protocol):
    def test(self, connection: DataConnection) -> tuple[bool, str]: ...

    def discover(self, connection: DataConnection) -> list[DiscoveredEntity]: ...

    def fetch_records(self, connection: DataConnection, *, entity_name: str, field_names: list[str], limit: int) -> list[dict]: ...


_REGISTRY: dict[str, Connector] = {}


def register(db_type: str, connector: Connector) -> None:
    _REGISTRY[db_type] = connector


def for_connection(connection: DataConnection) -> Connector | None:
    return _REGISTRY.get(connection.db_type or "")


def is_connector_type(db_type: str | None) -> bool:
    return (db_type or "") in _REGISTRY


def _load_builtin() -> None:
    from app.services.connectors import file_connector, rest_connector, rest_settings, sftp_connector  # noqa: F401  (each registers itself)


_load_builtin()
