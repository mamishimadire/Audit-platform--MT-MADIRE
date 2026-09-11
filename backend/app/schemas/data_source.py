import uuid
from datetime import datetime
from typing import Literal

from pydantic import model_validator

from app.schemas.common import OrmModel

Environment = Literal["on_premise", "cloud"]


class DataSourceCreate(OrmModel):
    source_name: str
    source_type: str  # e.g. postgresql, sql_server, mysql
    environment: Environment


class DataSourceOut(OrmModel):
    data_source_id: uuid.UUID
    organization_id: uuid.UUID
    source_name: str
    source_type: str
    environment: str
    status: str


DirectDbType = Literal["postgresql", "mysql", "mssql", "oracle", "sap_hana", "snowflake"]
OracleConnectionType = Literal["service_name", "sid"]
SnowflakeAuthMethod = Literal["password", "key_pair"]


class DataConnectionCreate(OrmModel):
    gateway_id: uuid.UUID | None = None
    secret_reference: str | None = None  # a label the gateway resolves locally — never a raw credential


class DirectConnectionCreate(OrmModel):
    db_type: DirectDbType
    host: str
    # Optional for Snowflake — its dialect resolves an account identifier
    # into a full hostname + port 443 internally, so this field is simply
    # not part of that connection shape.
    port: int | None = None
    # For Oracle, this holds either the Service Name or the SID — which one
    # is decided by oracle_connection_type, not by a second field, so the
    # form never asks the user to fill in both. Every other engine just
    # calls this the database name, as before. Optional for SAP HANA — a
    # tenant-DB connection needs only host+port, not a separate database
    # name (see data_source_service._direct_engine_url).
    database_name: str | None = None
    username: str
    # Write-only — never echoed back in any response. For Snowflake
    # key-pair auth, this field holds the PEM private key instead of a
    # password (same "one field, meaning chosen by a sibling field"
    # pattern as database_name for Oracle) — the label just changes in the UI.
    password: str
    oracle_connection_type: OracleConnectionType | None = None
    # SAP HANA-only: require TLS on the wire — defaults on, since on-premise
    # HANA instances (unlike every other engine here) vary in whether
    # TLS is already enforced by their own infrastructure.
    sap_hana_encrypt: bool = True
    # Snowflake-only.
    snowflake_warehouse: str | None = None
    snowflake_schema: str | None = None
    snowflake_role: str | None = None
    snowflake_auth_method: SnowflakeAuthMethod = "password"
    # Only used when snowflake_auth_method == 'key_pair' and the private
    # key itself is passphrase-protected.
    snowflake_key_passphrase: str | None = None

    @model_validator(mode="after")
    def _oracle_needs_a_connection_type(self) -> "DirectConnectionCreate":
        if self.db_type == "oracle" and self.oracle_connection_type is None:
            raise ValueError("Oracle connections must specify oracle_connection_type ('service_name' or 'sid').")
        return self

    @model_validator(mode="after")
    def _non_hana_needs_a_database_name(self) -> "DirectConnectionCreate":
        if self.db_type not in ("sap_hana", "snowflake") and not self.database_name:
            raise ValueError("database_name is required for this connection type.")
        return self

    @model_validator(mode="after")
    def _non_snowflake_needs_a_port(self) -> "DirectConnectionCreate":
        if self.db_type != "snowflake" and self.port is None:
            raise ValueError("port is required for this connection type.")
        return self

    @model_validator(mode="after")
    def _snowflake_needs_a_warehouse_database_and_schema(self) -> "DirectConnectionCreate":
        if self.db_type == "snowflake" and not (self.snowflake_warehouse and self.database_name and self.snowflake_schema):
            raise ValueError("Snowflake connections require snowflake_warehouse, database_name and snowflake_schema.")
        return self


class DataConnectionOut(OrmModel):
    connection_id: uuid.UUID
    data_source_id: uuid.UUID
    gateway_id: uuid.UUID | None
    connection_mode: str
    db_type: str | None = None
    host: str | None = None
    port: int | None = None
    database_name: str | None = None
    username: str | None = None
    oracle_connection_type: str | None = None
    sap_hana_encrypt: bool = True
    snowflake_warehouse: str | None = None
    snowflake_schema: str | None = None
    snowflake_role: str | None = None
    snowflake_auth_method: str = "password"
    connection_status: str
    last_tested_at: datetime | None


class ConnectionTestResult(OrmModel):
    success: bool
    detail: str | None = None


class DiscoveredField(OrmModel):
    field_name: str
    data_type: str | None = None
    is_primary_key: bool = False
    is_sensitive: bool = False


class DiscoveredEntity(OrmModel):
    entity_name: str
    entity_type: Literal["table", "view", "api", "file"] = "table"
    description: str | None = None
    fields: list[DiscoveredField] = []


class DiscoveryPayload(OrmModel):
    entities: list[DiscoveredEntity]


class DataEntityOut(OrmModel):
    entity_id: uuid.UUID
    data_source_id: uuid.UUID
    entity_name: str
    entity_type: str
    description: str | None


class DataFieldOut(OrmModel):
    field_id: uuid.UUID
    entity_id: uuid.UUID
    field_name: str
    data_type: str | None
    is_primary_key: bool
    is_sensitive: bool
