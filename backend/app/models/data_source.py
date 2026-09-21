import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    # User-facing label distinct from host/db/username — lets someone tell
    # apart several connection attempts against the same data source (see
    # the Data Sources page). Purely cosmetic, so renaming it still goes
    # through the same maker-checker path as every other edit below rather
    # than getting a silent-write exception carved out just for this field.
    connection_name: Mapped[str | None] = mapped_column(String(150))
    # Display-only declutter, not a state change — toggled directly with no
    # approval (see DevicePolicyPanel-style maker-checker below, which is
    # reserved for changes that affect what the connection actually does).
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
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
    # MongoDB-only — host holds the cluster address, database_name the
    # target database, username/encrypted_password reused as-is (all
    # already generic enough). mongodb_srv selects the connection string
    # scheme: True for `mongodb+srv://` (Atlas and most managed clusters —
    # a DNS SRV lookup resolves the real hosts/ports, so `port` is unused),
    # False for a plain `mongodb://host:port/` self-hosted/replica-set
    # deployment. See migration 0040.
    mongodb_srv: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Non-secret, family-specific settings for the connection families that are not databases (uploaded
    # files, SFTP, REST/SOAP APIs) — see migration 0083. Secrets never live here: they are one encrypted
    # JSON blob in encrypted_password.
    connector_config: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    connection_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    # When this connection's Gateway last reported measured relationships (see
    # gateway_relationship_service). NULL = never, and also "measure again".
    relationships_measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataConnectionChange(Base):
    """A proposed edit to, or disconnection of, a live DataConnection.

    Renaming/editing a connection's host, credentials, etc. — or taking it
    out of service — changes what the platform actually connects to and
    monitors, so (unlike DataConnection.is_hidden / DataEntity.is_hidden,
    which are pure display toggles) these go through independent approval,
    the same maker-checker shape used for device policy changes
    (device_policy_service.DevicePolicyChange). proposed_changes is stored
    using the DataConnection column names directly (encrypted_password, not
    password) so a pending secret is never sitting in this table in
    plaintext, and approval can apply it with a plain setattr loop.
    """

    __tablename__ = "data_connection_changes"

    change_id: Mapped[uuid.UUID] = uuid_pk("change_id")
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_connections.connection_id", ondelete="CASCADE"), nullable=False
    )
    change_type: Mapped[str] = mapped_column(String(20), nullable=False)
    proposed_changes: Mapped[dict] = mapped_column(JSONB, nullable=False)
    approval_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending_approval")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[str | None] = mapped_column(Text)


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
    # Display-only declutter (see DataConnection.is_hidden) — a hidden table
    # is still fully discovered/mapped-eligible, just collapsed out of the
    # default list. No approval needed to toggle this.
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


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


class DataFieldProfile(Base):
    """A sample-based summary of what one discovered column holds — see
    app/core/value_profile.py for what's stored and the privacy policy that
    decides whether `top_values` may hold real values."""

    __tablename__ = "data_field_profiles"

    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_fields.field_id", ondelete="CASCADE"), primary_key=True
    )
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    null_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    distinct_count: Mapped[int] = mapped_column(Integer, nullable=False)
    distinct_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    value_kind: Mapped[str | None] = mapped_column(String(20))
    # none_as_null: a column with no stored values is SQL NULL, not the JSON value `null`
    # (which `IS NULL` would not match).
    top_values: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))
    max_length: Mapped[int | None] = mapped_column(Integer)
    profiled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DataFieldConstraint(Base):
    """Catalog facts about one column: nullable / unique / indexed. None for
    is_nullable means the catalog did not say."""

    __tablename__ = "data_field_constraints"

    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_fields.field_id", ondelete="CASCADE"), primary_key=True
    )
    is_nullable: Mapped[bool | None] = mapped_column(Boolean)
    is_unique: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_indexed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class DataRelationship(Base):
    """One edge of the relationship graph: child column -> parent column.
    `declared_fk` is read from the client's schema; `inferred` was measured on
    the client's data (aggregate numbers only, never values). A row an auditor
    confirmed or rejected is never overwritten by re-discovery or re-inference."""

    __tablename__ = "data_relationships"

    relationship_id: Mapped[uuid.UUID] = uuid_pk("relationship_id")
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.data_source_id", ondelete="CASCADE"), nullable=False
    )
    child_field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_fields.field_id", ondelete="CASCADE"), nullable=False
    )
    parent_field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_fields.field_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    constraint_name: Mapped[str | None] = mapped_column(String(200))
    containment: Mapped[float | None] = mapped_column(Float)
    child_distinct: Mapped[int | None] = mapped_column(Integer)
    parent_distinct: Mapped[int | None] = mapped_column(Integer)
    parent_unique: Mapped[bool | None] = mapped_column(Boolean)
    cardinality: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="detected")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DataFile(Base):
    """One uploaded version of a file on a file connection. Bytes inline in Postgres (like evidence
    files). A new upload of the same file_name is a new version: the previous one is kept and
    is_current flips, so mappings keyed by the file's name survive re-uploads and every version keeps its
    sha256 for the audit trail."""

    __tablename__ = "data_files"

    file_id: Mapped[uuid.UUID] = uuid_pk("file_id")
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_connections.connection_id", ondelete="CASCADE"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Deferred: up to 25 MB per row must never come back with an ordinary load or with the refresh that follows a
    # commit. The connector fetches the bytes explicitly, only when it has to parse them.
    file_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
