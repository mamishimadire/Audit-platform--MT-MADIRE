from gateway.connectors.base import ConnectorConfig
from gateway.connectors.mongodb import MongoConnector
from gateway.connectors.mysql import MySqlConnector
from gateway.connectors.postgres import PostgresConnector
from gateway.connectors.sqlserver import SqlServerConnector

_REGISTRY = {
    "postgresql": PostgresConnector,
    "mysql": MySqlConnector,
    "sql_server": SqlServerConnector,
    "mongodb": MongoConnector,
}


def build_connector(source_type: str, config: ConnectorConfig):
    """
    Connector selection by source_type string — adding Oracle or another
    engine later means adding one class here, not touching the discovery
    or reporting logic (product spec Section 12/17: connector abstraction,
    never a single database type hard-coded into the application).
    """
    try:
        connector_cls = _REGISTRY[source_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported source_type '{source_type}'. Supported: {sorted(_REGISTRY)}") from exc
    return connector_cls(config)
