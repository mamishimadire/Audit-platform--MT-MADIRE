import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, uuid_pk


class Gateway(Base, TimestampMixin):
    __tablename__ = "gateways"

    gateway_id: Mapped[uuid.UUID] = uuid_pk("gateway_id")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    gateway_name: Mapped[str] = mapped_column(String(150), nullable=False)
    registration_code: Mapped[str | None] = mapped_column(String(20))
    registration_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registration_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="unused")
    device_certificate_fingerprint: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    version: Mapped[str | None] = mapped_column(String(20))
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    certificate_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )


class DataSource(Base, TimestampMixin):
    __tablename__ = "data_sources"

    data_source_id: Mapped[uuid.UUID] = uuid_pk("data_source_id")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    source_name: Mapped[str] = mapped_column(String(150), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    environment: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )


class DataConnection(Base, TimestampMixin):
    __tablename__ = "data_connections"

    connection_id: Mapped[uuid.UUID] = uuid_pk("connection_id")
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.data_source_id", ondelete="CASCADE"), nullable=False
    )
    gateway_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gateways.gateway_id", ondelete="SET NULL")
    )
    secret_reference: Mapped[str | None] = mapped_column(String(255))
    # 'gateway' (default, existing behavior): credential never reaches this
    # database, only a label the Gateway resolves against its own local
    # config. 'direct': the platform connects straight to a cloud-hosted
    # database itself, so the credential must live here — encrypted, never
    # in a response schema.
    connection_mode: Mapped[str] = mapped_column(String(20), nullable=False, server_default="gateway")
    db_type: Mapped[str | None] = mapped_column(String(20))
    host: Mapped[str | None] = mapped_column(String(255))
    port: Mapped[int | None] = mapped_column(Integer)
    database_name: Mapped[str | None] = mapped_column(String(150))
    username: Mapped[str | None] = mapped_column(String(150))
    encrypted_password: Mapped[str | None] = mapped_column(Text)
    # Oracle-only: whether database_name holds a Service Name (query-param
    # style URL) or a SID (path style, same shape every other engine
    # already uses) — see data_source_service._direct_engine_url.
    oracle_connection_type: Mapped[str | None] = mapped_column(String(20))
    # SAP HANA-only: require TLS on the wire (hdbcli's encrypt/
    # sslValidateCertificate). Defaults True — see migration 0035.
    sap_hana_encrypt: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Snowflake-only — host holds the account identifier and database_name
    # the database (both already generic enough to reuse); everything below
    # is genuinely Snowflake-specific. snowflake_auth_method selects what
    # encrypted_password actually holds: a password, or a PEM private key
    # for key-pair auth — same "one column, meaning chosen by a sibling
    # column" pattern as oracle_connection_type. See migration 0038.
    snowflake_warehouse: Mapped[str | None] = mapped_column(String(150))
    snowflake_schema: Mapped[str | None] = mapped_column(String(150))
    snowflake_role: Mapped[str | None] = mapped_column(String(150))
    snowflake_auth_method: Mapped[str] = mapped_column(String(20), nullable=False, server_default="password")
    encrypted_snowflake_key_passphrase: Mapped[str | None] = mapped_column(Text)
    connection_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )


class OAuthConnection(Base, TimestampMixin):
    """
    An OAuth-based API connection (HubSpot first) — deliberately a separate
    table from DataConnection's inline host/port/password columns, since
    this has its own lifecycle: a token pair that expires and refreshes on
    its own schedule, and can be revoked from the vendor's side at any time
    with no action on this platform. One row per DataConnection with
    connection_mode='oauth'. See migration 0037 for the full rationale.
    """

    __tablename__ = "oauth_connections"

    oauth_connection_id: Mapped[uuid.UUID] = uuid_pk("oauth_connection_id")
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_connections.connection_id", ondelete="CASCADE"), nullable=False, unique=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scope: Mapped[str | None] = mapped_column(Text)
    authorization_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    oauth_state: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )


class DataEntity(Base, TimestampMixin):
    __tablename__ = "data_entities"

    entity_id: Mapped[uuid.UUID] = uuid_pk("entity_id")
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.data_source_id", ondelete="CASCADE"), nullable=False
    )
    entity_name: Mapped[str] = mapped_column(String(150), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False, server_default="table")
    description: Mapped[str | None] = mapped_column(Text)


class DataField(Base):
    __tablename__ = "data_fields"

    field_id: Mapped[uuid.UUID] = uuid_pk("field_id")
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(150), nullable=False)
    data_type: Mapped[str | None] = mapped_column(String(50))
    is_primary_key: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)
