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


DirectDbType = Literal["postgresql", "mysql", "mssql", "oracle", "sap_hana", "snowflake", "mongodb"]
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
    # MongoDB-only. True (default) = `mongodb+srv://` — a DNS SRV lookup
    # resolves the real hosts/ports, so `port` is not needed (this is what
    # every MongoDB Atlas cluster uses, e.g. host="mamishi.xxxxx.mongodb.net").
    # False = a plain `mongodb://host:port/` self-hosted/replica-set
    # deployment, where port is then required like every other engine.
    mongodb_srv: bool = True

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
        # MongoDB needs a port only outside SRV mode — SRV resolves it via DNS.
        if self.db_type == "mongodb":
            if not self.mongodb_srv and self.port is None:
                raise ValueError("port is required for a MongoDB connection that isn't using mongodb+srv://.")
        elif self.db_type != "snowflake" and self.port is None:
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
    connection_name: str | None = None
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
    mongodb_srv: bool = True
    connection_status: str
    last_tested_at: datetime | None
    is_hidden: bool = False


ChangeType = Literal["update", "disconnect", "delete"]
ChangeApprovalStatus = Literal["pending_approval", "approved", "rejected"]


class DataConnectionUpdateRequest(OrmModel):
    """Every field is optional — only the ones actually being changed need
    be sent. At least one must be set (see the model_validator below);
    an empty request is never a valid "change" for a reviewer to weigh."""

    connection_name: str | None = None
    host: str | None = None
    port: int | None = None
    database_name: str | None = None
    username: str | None = None
    # Write-only, like DirectConnectionCreate.password — never round-tripped
    # back out of any response. Omit to leave the current credential as is.
    password: str | None = None
    oracle_connection_type: OracleConnectionType | None = None
    sap_hana_encrypt: bool | None = None
    snowflake_warehouse: str | None = None
    snowflake_schema: str | None = None
    snowflake_role: str | None = None
    snowflake_auth_method: SnowflakeAuthMethod | None = None
    snowflake_key_passphrase: str | None = None
    mongodb_srv: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "DataConnectionUpdateRequest":
        if not self.model_dump(exclude_none=True):
            raise ValueError("At least one field must be set to propose a connection change.")
        return self


class DataConnectionChangeOut(OrmModel):
    change_id: uuid.UUID
    connection_id: uuid.UUID
    change_type: ChangeType
    # Never echoes a raw password/passphrase back — see
    # data_connection_change_service for how those are stored pre-encrypted
    # under their live column names instead.
    proposed_changes: dict
    approval_status: ChangeApprovalStatus
    requested_by: uuid.UUID | None = None
    requested_at: datetime
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    rejected_by: uuid.UUID | None = None
    rejected_at: datetime | None = None
    rejected_reason: str | None = None


class DataConnectionRejectRequest(OrmModel):
    reason: str


class HiddenToggleRequest(OrmModel):
    hidden: bool


class ConnectionTestResult(OrmModel):
    success: bool
    detail: str | None = None


class DiscoveredFieldProfile(OrmModel):
    """A Gateway-computed column profile (see app.core.value_profile). Shape
    only: the platform re-screens it (sanitize_reported_profile) before
    storing anything, and never trusts `top_values` on its own say-so."""

    sample_size: int
    null_ratio: float
    distinct_count: int
    distinct_ratio: float
    value_kind: str | None = None
    top_values: list[str] | None = None
    max_length: int | None = None


class DiscoveredField(OrmModel):
    field_name: str
    data_type: str | None = None
    is_primary_key: bool = False
    is_sensitive: bool = False
    # Optional: a Gateway that predates column profiling (or has it turned
    # off) simply doesn't send one, and any existing profile is kept.
    profile: DiscoveredFieldProfile | None = None
    # Catalog facts (see app.core.schema_metadata). None = the catalog didn't say.
    is_nullable: bool | None = None
    is_unique: bool | None = None
    is_indexed: bool | None = None


class DiscoveredForeignKey(OrmModel):
    name: str | None = None
    columns: list[str]
    referred_table: str
    referred_columns: list[str]


class DiscoveredEntity(OrmModel):
    entity_name: str
    # 'collection' — MongoDB: never labeled 'table', since it genuinely
    # isn't one (no fixed columns, no schema enforcement at the database
    # level) — see mongo_connector.py.
    entity_type: Literal["table", "view", "api", "file", "collection"] = "table"
    description: str | None = None
    fields: list[DiscoveredField] = []
    # None = not reported (an older Gateway, a dialect that can't say, or a
    # failed read): relationships already known are kept. [] = reported, none exist.
    foreign_keys: list[DiscoveredForeignKey] | None = None


class DiscoveryPayload(OrmModel):
    entities: list[DiscoveredEntity]


class DataEntityOut(OrmModel):
    entity_id: uuid.UUID
    data_source_id: uuid.UUID
    entity_name: str
    entity_type: str
    description: str | None
    is_hidden: bool = False


class DataFieldOut(OrmModel):
    field_id: uuid.UUID
    entity_id: uuid.UUID
    field_name: str
    data_type: str | None
    is_primary_key: bool
    is_sensitive: bool


class RelationshipPairRequest(OrmModel):
    """A column pair a Gateway is asked to measure on its own database."""

    child_field_id: uuid.UUID
    parent_field_id: uuid.UUID
    child_entity: str
    child_column: str
    child_type: str | None = None
    parent_entity: str
    parent_column: str
    parent_type: str | None = None


class RelationshipRequestOut(OrmModel):
    connection_id: uuid.UUID
    pairs: list[RelationshipPairRequest]


class RelationshipMeasurementIn(OrmModel):
    """Counts only: how many distinct child values there are, how many of them exist
    on the parent side, and how many distinct values / rows the parent has."""

    child_field_id: uuid.UUID
    parent_field_id: uuid.UUID
    child_distinct: int
    matched_distinct: int
    parent_distinct: int
    parent_rows: int | None = None
    capped: bool = False


class RelationshipMeasurementsIn(OrmModel):
    measurements: list[RelationshipMeasurementIn]
