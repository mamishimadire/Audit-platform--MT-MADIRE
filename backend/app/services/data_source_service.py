import logging
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import MetaData, String, Table, cast, create_engine, delete, distinct, func, insert, inspect, select, text, tuple_, update
from sqlalchemy import column as sql_column, table as sql_table
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine, URL
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.net_guard import UnsafeDestination, resolve_public
from app.core.relationship_inference import (
    Candidate,
    ColumnRef,
    Measurement,
    generate_candidates,
    score_inferred,
    worth_storing,
)
from app.core.schema_metadata import derive_column_constraints, normalize_foreign_keys
from app.core.value_profile import ColumnProfile, build_column_profile, sanitize_reported_profile
from app.models.control_library import ControlTableBinding
from app.models.data_source import (
    DataConnection,
    DataEntity,
    DataField,
    DataFieldConstraint,
    DataFieldProfile,
    DataRelationship,
    DataSource,
)
from app.schemas.data_source import (
    ConnectorConnectionCreate,
    DataConnectionCreate,
    DataSourceCreate,
    DirectConnectionCreate,
    DiscoveredEntity,
    DiscoveredField,
    DiscoveryPayload,
)
from app.services import connectors
from app.services.audit_log_service import log_action
from app.services.connectors import config as connector_settings, presets

logger = logging.getLogger(__name__)

# Short, fixed connect timeout regardless of DB type — a direct connection
# targets a host/port the caller supplies, so a slow or unreachable target
# must fail fast rather than tying up a backend worker indefinitely.
_CONNECT_TIMEOUT_SECONDS = 8

# The most rows the platform reads from a file or API connection for one analysis pass (relationship
# measurement, distinct-value checks). A file is already parsed in memory; an API connector applies its
# own tighter page limits below this.
_CONNECTOR_ROW_CAP = 100_000

_DIRECT_DRIVER_BY_TYPE = {
    "postgresql": "postgresql+psycopg",
    "mysql": "mysql+pymysql",
    "mssql": "mssql+pymssql",
    # Thin mode (the oracledb default) needs no Oracle Client install on
    # this machine or the Gateway — important since the Gateway is meant
    # to stay lightweight.
    "oracle": "oracle+oracledb",
    # sqlalchemy-hana + hdbcli — pure-Python client, same "no separate
    # engine-vendor install on the Gateway" property as Oracle thin mode.
    "sap_hana": "hana+hdbcli",
    # snowflake-sqlalchemy + snowflake-connector-python.
    "snowflake": "snowflake",
}


def _direct_connect_args(connection: DataConnection) -> dict:
    if connection.db_type == "mssql":
        return {"timeout": _CONNECT_TIMEOUT_SECONDS, "login_timeout": _CONNECT_TIMEOUT_SECONDS}
    if connection.db_type == "oracle":
        return {"tcp_connect_timeout": _CONNECT_TIMEOUT_SECONDS}
    if connection.db_type == "sap_hana":
        # hdbcli's own connect kwargs — communicationTimeout is milliseconds,
        # not seconds, unlike every other driver here.
        return {
            "communicationTimeout": _CONNECT_TIMEOUT_SECONDS * 1000,
            "encrypt": bool(connection.sap_hana_encrypt),
            "sslValidateCertificate": bool(connection.sap_hana_encrypt),
        }
    if connection.db_type == "snowflake":
        args: dict = {"login_timeout": _CONNECT_TIMEOUT_SECONDS}
        if connection.snowflake_auth_method == "key_pair":
            # encrypted_password holds the PEM private key for this mode
            # (see the model's docstring) — decoded into the DER/PKCS8 bytes
            # snowflake-connector-python's `private_key` kwarg expects, never
            # passed through the URL so it can't end up in a connection-string
            # log line the way a password conceptually could.
            from cryptography.hazmat.primitives import serialization

            pem_bytes = decrypt_secret(connection.encrypted_password).encode()
            passphrase = (
                decrypt_secret(connection.encrypted_snowflake_key_passphrase).encode()
                if connection.encrypted_snowflake_key_passphrase
                else None
            )
            private_key = serialization.load_pem_private_key(pem_bytes, password=passphrase)
            args["private_key"] = private_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        return args
    return {"connect_timeout": _CONNECT_TIMEOUT_SECONDS}


def _direct_engine_url(connection: DataConnection, *, password: str) -> URL:
    driver = _DIRECT_DRIVER_BY_TYPE[connection.db_type]
    # Oracle's Service Name is a connect-time query parameter, not part of
    # the path — every other engine (and Oracle's SID form) puts the
    # database identifier in the path, which URL.create's `database` arg
    # already does. Building via URL.create (not an f-string) also means a
    # password containing '@', ':' or '%' can't break the connection
    # string for any engine, not just this one.
    if connection.db_type == "oracle" and connection.oracle_connection_type == "service_name":
        return URL.create(
            driver, username=connection.username, password=password,
            host=connection.host, port=connection.port,
            query={"service_name": connection.database_name},
        )
    # A HANA tenant-DB connection needs only host+port — no separate
    # database/tenant name segment (see sqlalchemy_hana's create_connect_args,
    # which only reads `database` for the rarer SYSTEMDB-routed MDC case).
    if connection.db_type == "sap_hana":
        return URL.create(
            driver, username=connection.username, password=password,
            host=connection.host, port=connection.port,
        )
    if connection.db_type == "snowflake":
        # snowflake-sqlalchemy splits `database` on "/" into (database,
        # schema) itself — see its create_connect_args. `host` here is the
        # account identifier; the dialect resolves the real hostname/port.
        # key-pair auth passes the key via connect_args, never the URL, so
        # the password slot is left empty for that mode.
        query = {"warehouse": connection.snowflake_warehouse}
        if connection.snowflake_role:
            query["role"] = connection.snowflake_role
        return URL.create(
            driver, username=connection.username,
            password=password if connection.snowflake_auth_method == "password" else None,
            host=connection.host, database=f"{connection.database_name}/{connection.snowflake_schema}",
            query=query,
        )
    return URL.create(
        driver, username=connection.username, password=password,
        host=connection.host, port=connection.port, database=connection.database_name,
    )


_DEFAULT_DIRECT_PORTS = {"postgresql": 5432, "mysql": 3306, "mssql": 1433, "oracle": 1521, "sap_hana": 443}
_SNOWFLAKE_ACCOUNT = re.compile(r"^[a-z0-9][a-z0-9._-]{0,120}$")


def _direct_destination(connection: DataConnection) -> tuple[str, int]:
    """The host and port the platform will actually dial for a direct SQL connection. For Snowflake the
    `host` is an account identifier and the driver dials <account>.snowflakecomputing.com."""
    if connection.db_type == "snowflake":
        account = (connection.host or "").strip().lower()
        if not _SNOWFLAKE_ACCOUNT.match(account):
            raise ValueError("The Snowflake account identifier is not valid.")
        suffixes = (".snowflakecomputing.com", ".snowflakecomputing.cn")
        return (account if account.endswith(suffixes) else f"{account}.snowflakecomputing.com"), 443
    return (connection.host or "").strip(), connection.port or _DEFAULT_DIRECT_PORTS.get(connection.db_type, 443)


def _vet_direct_destination(connection: DataConnection) -> None:
    """The platform only dials the public internet (see app.core.net_guard): a host that resolves to a
    private, loopback, link-local or metadata address is refused BEFORE any driver is asked to connect,
    so a connection cannot be used to probe the platform's own network. Every address the name resolves
    to must be public. (The driver then resolves the name itself, so this narrows rather than removes a
    DNS-rebinding window; the file, SFTP and API connections do pin the vetted address.)"""
    host, port = _direct_destination(connection)
    try:
        resolve_public(host, port)
    except UnsafeDestination as exc:
        raise ValueError(str(exc)) from exc


def _build_direct_engine(connection: DataConnection) -> Engine:
    _vet_direct_destination(connection)
    password = decrypt_secret(connection.encrypted_password)
    url = _direct_engine_url(connection, password=password)
    return create_engine(url, connect_args=_direct_connect_args(connection), pool_pre_ping=False)


def create_data_source(
    db: Session, *, organization_id: uuid.UUID, payload: DataSourceCreate, created_by_user_id: uuid.UUID
) -> DataSource:
    source = DataSource(
        organization_id=organization_id,
        source_name=payload.source_name,
        source_type=payload.source_type,
        environment=payload.environment,
        status="pending",
        created_by=created_by_user_id,
    )
    db.add(source)
    db.flush()
    log_action(
        db,
        action=f"Registered data source '{source.source_name}'",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="data_sources",
        entity_id=source.data_source_id,
        new_value={"source_name": source.source_name, "source_type": source.source_type},
    )
    db.commit()
    db.refresh(source)
    return source


def list_data_sources(db: Session, *, organization_id: uuid.UUID) -> list[DataSource]:
    return list(db.scalars(select(DataSource).where(DataSource.organization_id == organization_id)))


def create_connection(
    db: Session, *, data_source_id: uuid.UUID, payload: DataConnectionCreate, created_by_user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> DataConnection:
    # A slow response plus a double-click (no debounce on the button) was
    # creating several identical pending connections for the same data
    # source/gateway pair — reject the duplicate rather than silently
    # accumulating rows that all point at the same place.
    existing = db.scalar(
        select(DataConnection).where(
            DataConnection.data_source_id == data_source_id,
            DataConnection.gateway_id.is_(payload.gateway_id) if payload.gateway_id is None else DataConnection.gateway_id == payload.gateway_id,
        )
    )
    if existing is not None:
        raise ValueError("A connection already exists for this data source and gateway")

    connection = DataConnection(
        data_source_id=data_source_id,
        gateway_id=payload.gateway_id,
        secret_reference=payload.secret_reference,
        connection_status="pending",
        created_by=created_by_user_id,
    )
    db.add(connection)
    db.flush()
    log_action(
        db,
        action="Created data connection",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="data_connections",
        entity_id=connection.connection_id,
        new_value={"data_source_id": str(data_source_id), "gateway_id": str(payload.gateway_id) if payload.gateway_id else None},
    )
    db.commit()
    db.refresh(connection)
    return connection


def create_direct_connection(
    db: Session, *, data_source_id: uuid.UUID, payload: DirectConnectionCreate, created_by_user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> DataConnection:
    connector_settings.refuse_internal_literal(payload.host)
    existing = db.scalar(
        select(DataConnection).where(
            DataConnection.data_source_id == data_source_id,
            DataConnection.connection_mode == "direct",
            DataConnection.host == payload.host,
            DataConnection.database_name == payload.database_name,
            DataConnection.username == payload.username,
        )
    )
    if existing is not None:
        raise ValueError("A direct connection with this host, database and username already exists for this data source")

    connection = DataConnection(
        data_source_id=data_source_id,
        connection_mode="direct",
        db_type=payload.db_type,
        host=payload.host,
        port=payload.port,
        database_name=payload.database_name,
        username=payload.username,
        encrypted_password=encrypt_secret(payload.password),
        oracle_connection_type=payload.oracle_connection_type,
        sap_hana_encrypt=payload.sap_hana_encrypt,
        snowflake_warehouse=payload.snowflake_warehouse,
        snowflake_schema=payload.snowflake_schema,
        snowflake_role=payload.snowflake_role,
        snowflake_auth_method=payload.snowflake_auth_method,
        encrypted_snowflake_key_passphrase=(
            encrypt_secret(payload.snowflake_key_passphrase) if payload.snowflake_key_passphrase else None
        ),
        mongodb_srv=payload.mongodb_srv,
        connection_status="pending",
        created_by=created_by_user_id,
    )
    db.add(connection)
    db.flush()
    log_action(
        db,
        action="Created direct cloud data connection",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="data_connections",
        entity_id=connection.connection_id,
        # Never the password — host/db/username identify the connection without exposing the secret.
        new_value={"data_source_id": str(data_source_id), "db_type": payload.db_type, "host": payload.host, "database_name": payload.database_name},
    )
    db.commit()
    db.refresh(connection)
    return connection


def create_connector_connection(
    db: Session, *, data_source_id: uuid.UUID, payload: ConnectorConnectionCreate, created_by_user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> DataConnection:
    """A file / SFTP / API connection. Its settings are validated per family (a ValueError with a message
    fit to show), its credentials are stored as one encrypted blob, and nothing secret is logged."""
    config = payload.config
    if payload.preset:
        if payload.db_type != "rest_api":
            raise connector_settings.ConfigError("A preset is a template for a REST API connection.")
        if payload.config:
            raise connector_settings.ConfigError("Give either a preset or the settings, not both. Edit the connection afterwards to add to a preset.")
        config = presets.expand(payload.preset, payload.preset_params)
    elif payload.preset_params:
        raise connector_settings.ConfigError("preset_params were given without a preset.")
    prepared = connector_settings.prepare(payload.db_type, config, payload.secrets)
    if prepared.host is not None:
        same_server = db.scalars(
            select(DataConnection).where(
                DataConnection.data_source_id == data_source_id,
                DataConnection.connection_mode == "direct",
                DataConnection.db_type == payload.db_type,
                DataConnection.host == prepared.host,
                DataConnection.port == prepared.port,
                DataConnection.username == prepared.username,
            )
        ).all()
        if any((c.connector_config or {}) == (prepared.config or {}) for c in same_server):
            raise ValueError("This data source already has a connection with the same server, user and settings.")

    connection = DataConnection(
        data_source_id=data_source_id,
        connection_mode="direct",
        db_type=payload.db_type,
        connection_name=payload.connection_name,
        host=prepared.host,
        port=prepared.port,
        username=prepared.username,
        encrypted_password=connector_settings.pack_secrets(prepared.secrets),
        connector_config=prepared.config,
        connection_status="pending",
        created_by=created_by_user_id,
    )
    db.add(connection)
    db.flush()
    log_action(
        db,
        action=f"Created {payload.db_type.replace('_', ' ')} data connection",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="data_connections",
        entity_id=connection.connection_id,
        new_value={"data_source_id": str(data_source_id), "db_type": payload.db_type, "host": prepared.host, "username": prepared.username},
    )
    db.commit()
    db.refresh(connection)
    return connection


def test_direct_connection(db: Session, *, connection: DataConnection) -> tuple[bool, str]:
    """Actually connects to the target database — the only way to know a
    direct (non-Gateway) connection works, since there's no Gateway to ask."""
    connector = connectors.for_connection(connection)
    if connector is not None:
        try:
            success, detail = connector.test(connection)
        except connectors.ConnectorError as exc:
            success, detail = False, str(exc)  # written to be shown
        except Exception:  # noqa: BLE001 — a network/parser error can echo an address or a credential
            success, detail = False, "Could not reach this connection — check its settings and that it is reachable from the platform."
        record_connection_test_result(db, connection=connection, success=success)
        return success, detail

    if connection.db_type == "mongodb":
        # Not a SQLAlchemy engine — MongoDB has its own driver/connector
        # module entirely (see mongo_connector.py's docstring for why).
        from app.services.mongo_connector import test_mongo_connection

        success, detail = test_mongo_connection(connection)
        record_connection_test_result(db, connection=connection, success=success)
        return success, detail

    try:
        engine = _build_direct_engine(connection)
        with engine.connect():
            pass
        engine.dispose()
        success, detail = True, "Connected successfully."
    except ValueError as exc:
        success, detail = False, str(exc)  # our own decrypt-failure message — safe to show
    except Exception:  # noqa: BLE001 — driver exceptions can echo the DSN (incl. password); never surface raw
        success, detail = False, "Could not connect — check the host, port, credentials, and that this address is reachable from the platform."

    record_connection_test_result(db, connection=connection, success=success)
    return success, detail


def discover_direct_connection_schema(db: Session, *, connection: DataConnection) -> list[DataEntity]:
    # Entities are pooled under the data source, not owned by a connection, so discovery from one of several
    # connections must never delete the tables another one supplies (with their mappings). That is
    # replace_discovery's decision (it has the session); it is told which connection is reporting.
    reporter = getattr(connection, "connection_id", None)

    connector = connectors.for_connection(connection)
    if connector is not None:
        entities = connector.discover(connection)
        return replace_discovery(
            db, data_source_id=connection.data_source_id, payload=DiscoveryPayload(entities=entities), reported_by_connection=reporter
        )

    if connection.db_type == "mongodb":
        from app.services.mongo_connector import discover_mongo_schema

        entities = discover_mongo_schema(connection)
        return replace_discovery(
            db, data_source_id=connection.data_source_id, payload=DiscoveryPayload(entities=entities), reported_by_connection=reporter
        )

    engine = _build_direct_engine(connection)
    try:
        inspector = inspect(engine)
        entities: list[DiscoveredEntity] = []
        table_names = inspector.get_table_names()
        # One catalog query per FACT for the whole schema (SQLAlchemy 2.0's get_multi_*; native on
        # PostgreSQL, Oracle and others) instead of one per fact PER TABLE: a remote database costs a
        # network round trip each, which made a 17-table schema take over a minute. A dialect or call
        # that can't batch falls back to the per-table read for that fact alone.
        columns_by = _batched(inspector, "get_multi_columns")
        pks_by = _batched(inspector, "get_multi_pk_constraint")
        uniques_by = _batched(inspector, "get_multi_unique_constraints")
        indexes_by = _batched(inspector, "get_multi_indexes")
        fks_by = _batched(inspector, "get_multi_foreign_keys")
        for table_name in table_names:
            columns = columns_by[table_name] if columns_by is not None and table_name in columns_by else inspector.get_columns(table_name)
            pk = pks_by[table_name] if pks_by is not None and table_name in pks_by else inspector.get_pk_constraint(table_name)
            pk_columns = set((pk or {}).get("constrained_columns") or [])
            # Catalog facts beyond names/types. Each read is best-effort: a
            # dialect that can't report one (Snowflake has no indexes, HANA and
            # Snowflake rarely declare foreign keys) or a permission gap must
            # never fail discovery — the fact is simply "not reported".
            unique_constraints = uniques_by[table_name] if uniques_by is not None and table_name in uniques_by else _safe_inspector_call(inspector.get_unique_constraints, table_name)
            indexes = indexes_by[table_name] if indexes_by is not None and table_name in indexes_by else _safe_inspector_call(inspector.get_indexes, table_name)
            raw_fks = fks_by[table_name] if fks_by is not None and table_name in fks_by else _safe_inspector_call(inspector.get_foreign_keys, table_name)
            foreign_keys = normalize_foreign_keys(raw_fks)
            facts = derive_column_constraints(
                [col["name"] for col in columns],
                nullable_by_column={col["name"]: col.get("nullable") for col in columns},
                pk_columns=pk_columns,
                unique_constraints=unique_constraints,
                indexes=indexes,
            )
            fields = [
                DiscoveredField(
                    field_name=col["name"],
                    data_type=str(col.get("type")),
                    is_primary_key=col["name"] in pk_columns,
                    is_nullable=facts[col["name"]]["is_nullable"],
                    # Only claim "not unique / not indexed" when the catalog was actually read.
                    is_unique=facts[col["name"]]["is_unique"] if (unique_constraints is not None or pk_columns) else None,
                    is_indexed=facts[col["name"]]["is_indexed"] if indexes is not None else None,
                )
                for col in columns
            ]
            entities.append(
                DiscoveredEntity(entity_name=table_name, entity_type="table", fields=fields, foreign_keys=foreign_keys)
            )
    finally:
        engine.dispose()

    return replace_discovery(
        db, data_source_id=connection.data_source_id, payload=DiscoveryPayload(entities=entities), reported_by_connection=reporter
    )


def _batched(inspector, method_name: str) -> dict[str, object] | None:
    """{table name: result} from one of the inspector's get_multi_* calls for the default schema, or
    None when the dialect or the call can't do it (the caller then reads table by table)."""
    try:
        return {table: result for (_schema, table), result in getattr(inspector, method_name)().items()}
    except Exception:  # noqa: BLE001 — unsupported, or a permission gap on one catalog view
        return None


def _safe_inspector_call(call, table_name: str):
    try:
        return call(table_name)
    except Exception:  # noqa: BLE001 — unsupported by this dialect/permissions; discovery carries on without it
        return None


def list_connections(db: Session, *, data_source_id: uuid.UUID) -> list[DataConnection]:
    return list(db.scalars(select(DataConnection).where(DataConnection.data_source_id == data_source_id)))


def record_connection_test_result(db: Session, *, connection: DataConnection, success: bool) -> DataConnection:
    connection.connection_status = "connected" if success else "failed"
    connection.last_tested_at = datetime.now(timezone.utc)
    data_source = db.get(DataSource, connection.data_source_id)
    if data_source is not None:
        data_source.status = "connected" if success else "error"
    db.commit()
    db.refresh(connection)
    return connection


def replace_discovery(
    db: Session, *, data_source_id: uuid.UUID, payload: DiscoveryPayload, reported_by_connection: uuid.UUID | None = None
) -> list[DataEntity]:
    """
    `reported_by_connection`: the direct connection this report came from. Entities are pooled under the data
    source, so when the source has OTHER direct connections an entity missing from this report may simply belong
    to one of them: nothing is removed then (fields of a table that IS reported are still reconciled). None
    (a Gateway's report, which covers the whole source) prunes as before.

    Reconciles discovered entities/fields against what's already stored,
    rather than deleting and recreating everything. That distinction is not
    cosmetic: entity_id/field_id are what test_data_mappings points at
    (ON DELETE CASCADE), so blindly replacing every row on every discovery
    run would silently destroy every approved mapping for that data source
    the next time discovery happens — discovered the hard way via a live
    audit-test-execution test that came back with zero mappings after a
    second discovery pass. Existing entities/fields are matched by name and
    keep their IDs; only genuinely new ones are inserted and genuinely
    removed ones deleted — done as three bulk operations (not one round
    trip per row) to stay fast against a remote database like Neon.
    """
    existing_entities = {e.entity_name: e for e in db.scalars(select(DataEntity).where(DataEntity.data_source_id == data_source_id))}
    existing_fields_by_entity: dict[uuid.UUID, dict[str, DataField]] = {}
    if existing_entities:
        entity_ids = [e.entity_id for e in existing_entities.values()]
        for field in db.scalars(select(DataField).where(DataField.entity_id.in_(entity_ids))):
            existing_fields_by_entity.setdefault(field.entity_id, {})[field.field_name] = field

    prune_missing = True
    if reported_by_connection is not None:
        others = db.scalar(
            select(func.count()).select_from(DataConnection).where(
                DataConnection.data_source_id == data_source_id,
                DataConnection.connection_mode == "direct",
                DataConnection.connection_id != reported_by_connection,
            )
        )
        prune_missing = not others

    seen_entity_names: set[str] = set()
    reported_profiles: dict[tuple[str, str], object] = {}
    reported_constraints: dict[tuple[str, str], tuple] = {}
    reported_foreign_keys: dict[str, list] = {}
    new_entity_rows = []
    new_field_rows = []
    stale_field_ids: list[uuid.UUID] = []
    result_entity_ids: list[uuid.UUID] = []

    for discovered in payload.entities:
        seen_entity_names.add(discovered.entity_name)
        if discovered.foreign_keys is not None:
            reported_foreign_keys[discovered.entity_name] = discovered.foreign_keys
        existing = existing_entities.get(discovered.entity_name)
        if existing is not None:
            existing.entity_type = discovered.entity_type
            existing.description = discovered.description
            entity_id = existing.entity_id
        else:
            entity_id = uuid.uuid4()
            new_entity_rows.append(
                {
                    "entity_id": entity_id,
                    "data_source_id": data_source_id,
                    "entity_name": discovered.entity_name,
                    "entity_type": discovered.entity_type,
                    "description": discovered.description,
                }
            )
        result_entity_ids.append(entity_id)

        existing_fields = existing_fields_by_entity.get(entity_id, {})
        seen_field_names = set()
        for f in discovered.fields:
            seen_field_names.add(f.field_name)
            if f.profile is not None:
                reported_profiles[(discovered.entity_name, f.field_name)] = f.profile
            if f.is_nullable is not None or f.is_unique is not None or f.is_indexed is not None:
                reported_constraints[(discovered.entity_name, f.field_name)] = (f.is_nullable, f.is_unique, f.is_indexed)
            existing_field = existing_fields.get(f.field_name)
            if existing_field is not None:
                existing_field.data_type = f.data_type
                existing_field.is_primary_key = f.is_primary_key
                # is_sensitive is an auditor-set judgement call (Section 12/24) — never overwritten by re-discovery.
            else:
                new_field_rows.append(
                    {
                        "field_id": uuid.uuid4(),
                        "entity_id": entity_id,
                        "field_name": f.field_name,
                        "data_type": f.data_type,
                        "is_primary_key": f.is_primary_key,
                        "is_sensitive": f.is_sensitive,
                    }
                )
        for name, field in existing_fields.items():
            if name not in seen_field_names:
                stale_field_ids.append(field.field_id)  # column genuinely no longer exists at the source

    # prune_missing=False: this report covers only one of several connections that share the source,
    # so an entity it doesn't list may simply belong to another one.
    stale_entity_ids = (
        [e.entity_id for name, e in existing_entities.items() if name not in seen_entity_names] if prune_missing else []
    )

    if stale_field_ids:
        db.execute(delete(DataField).where(DataField.field_id.in_(stale_field_ids)))
    if stale_entity_ids:
        db.execute(delete(DataEntity).where(DataEntity.entity_id.in_(stale_entity_ids)))
    if new_entity_rows:
        db.execute(insert(DataEntity), new_entity_rows)
    if new_field_rows:
        db.execute(insert(DataField), new_field_rows)
    if reported_profiles:
        _store_reported_profiles(db, data_source_id=data_source_id, reported=reported_profiles)
    if reported_constraints or reported_foreign_keys:
        _store_reported_schema_metadata(
            db, data_source_id=data_source_id, constraints=reported_constraints, foreign_keys=reported_foreign_keys
        )

    db.commit()
    return list(db.scalars(select(DataEntity).where(DataEntity.entity_id.in_(result_entity_ids))))


def list_entities(db: Session, *, data_source_id: uuid.UUID) -> list[DataEntity]:
    return list(db.scalars(select(DataEntity).where(DataEntity.data_source_id == data_source_id)))


def list_fields(db: Session, *, entity_id: uuid.UUID) -> list[DataField]:
    return list(db.scalars(select(DataField).where(DataField.entity_id == entity_id)))


def list_connections_for_organization(db: Session, *, organization_id: uuid.UUID) -> list[DataConnection]:
    return list(
        db.scalars(
            select(DataConnection)
            .join(DataSource, DataSource.data_source_id == DataConnection.data_source_id)
            .where(DataSource.organization_id == organization_id)
        )
    )


def set_entity_hidden(db: Session, *, entity: DataEntity, hidden: bool, organization_id: uuid.UUID, updated_by_user_id: uuid.UUID) -> DataEntity:
    """Pure display declutter — see DataEntity.is_hidden. No approval: it
    doesn't change what's discovered or mappable, only what the default
    table list shows."""
    entity.is_hidden = hidden
    log_action(
        db,
        action=f"{'Hid' if hidden else 'Unhid'} discovered table '{entity.entity_name}'",
        organization_id=organization_id,
        user_id=updated_by_user_id,
        entity_type="data_entities",
        entity_id=entity.entity_id,
        new_value={"is_hidden": hidden},
    )
    db.commit()
    db.refresh(entity)
    return entity


def set_connection_hidden(
    db: Session, *, connection: DataConnection, hidden: bool, organization_id: uuid.UUID, updated_by_user_id: uuid.UUID
) -> DataConnection:
    """Same display-only toggle as set_entity_hidden, for decluttering a
    connection list full of old failed attempts. Never touches
    connection_status — a hidden connection is unaffected otherwise."""
    connection.is_hidden = hidden
    log_action(
        db,
        action=f"{'Hid' if hidden else 'Unhid'} data connection",
        organization_id=organization_id,
        user_id=updated_by_user_id,
        entity_type="data_connections",
        entity_id=connection.connection_id,
        new_value={"is_hidden": hidden},
    )
    db.commit()
    db.refresh(connection)
    return connection


def set_all_entities_hidden(
    db: Session, *, data_source_id: uuid.UUID, hidden: bool, organization_id: uuid.UUID, updated_by_user_id: uuid.UUID
) -> int:
    """Bulk version of set_entity_hidden — one UPDATE statement rather than
    one round trip per table, since a data source can have well over a
    hundred discovered tables (see the control-library demo dataset)."""
    ids = list(
        db.scalars(
            select(DataEntity.entity_id).where(DataEntity.data_source_id == data_source_id, DataEntity.is_hidden != hidden)
        )
    )
    if not ids:
        return 0
    db.execute(update(DataEntity).where(DataEntity.entity_id.in_(ids)).values(is_hidden=hidden))
    log_action(
        db,
        action=f"{'Hid' if hidden else 'Unhid'} all discovered tables ({len(ids)})",
        organization_id=organization_id,
        user_id=updated_by_user_id,
        entity_type="data_entities",
        new_value={"is_hidden": hidden, "count": len(ids)},
    )
    db.commit()
    return len(ids)


def set_all_connections_hidden(
    db: Session, *, data_source_id: uuid.UUID, hidden: bool, organization_id: uuid.UUID, updated_by_user_id: uuid.UUID
) -> int:
    ids = list(
        db.scalars(
            select(DataConnection.connection_id).where(
                DataConnection.data_source_id == data_source_id, DataConnection.is_hidden != hidden
            )
        )
    )
    if not ids:
        return 0
    db.execute(update(DataConnection).where(DataConnection.connection_id.in_(ids)).values(is_hidden=hidden))
    log_action(
        db,
        action=f"{'Hid' if hidden else 'Unhid'} all connections ({len(ids)})",
        organization_id=organization_id,
        user_id=updated_by_user_id,
        entity_type="data_connections",
        new_value={"is_hidden": hidden, "count": len(ids)},
    )
    db.commit()
    return len(ids)


def _sample_distinct_sql_values(connection: DataConnection, *, table_name: str, column_name: str, limit: int) -> set[str] | None:
    """Table/column reflection (not raw string interpolation) so identifier
    quoting is handled correctly per-dialect — table_name/column_name come
    from our own discovery metadata (the target database's real catalog),
    never end-user input, but reflecting through SQLAlchemy Core is still
    the right way to build this query rather than hand-quoting per engine."""
    engine = _build_direct_engine(connection)
    try:
        metadata = MetaData()
        table = Table(table_name, metadata, autoload_with=engine)
        if column_name not in table.c:
            return None
        with engine.connect() as conn:
            rows = conn.execute(select(distinct(table.c[column_name])).limit(limit))
            return {str(row[0]) for row in rows if row[0] is not None}
    finally:
        engine.dispose()


def sample_distinct_values(db: Session, *, data_source_id: uuid.UUID, entity_name: str, field_name: str, limit: int = 500) -> set[str] | None:
    """For relationship validation: the actual distinct values a mapped
    field holds right now, sourced from whichever of this data source's
    direct connections is currently connected. None means "couldn't check"
    (no direct connection available — e.g. Gateway-only, or the query
    itself failed), never an empty set standing in for "we don't know."
    A data source can have several direct connections (see the Data
    Sources page); entities/fields aren't tied to a specific one of them
    (replace_discovery pools them under the data source), so this tries
    each connected direct connection in turn rather than assuming a single
    owner.
    """
    connections = db.scalars(
        select(DataConnection).where(
            DataConnection.data_source_id == data_source_id,
            DataConnection.connection_mode == "direct",
            DataConnection.connection_status == "connected",
        )
    ).all()
    for connection in connections:
        try:
            connector = connectors.for_connection(connection)
            if connector is not None:
                rows = connector.fetch_records(connection, entity_name=entity_name, field_names=[field_name], limit=_CONNECTOR_ROW_CAP)
                values: set[str] = set()
                for row in rows:
                    value = row.get(field_name)
                    if value is not None:
                        values.add(str(value))
                        if len(values) >= limit:
                            break
                return values
            if connection.db_type == "mongodb":
                from app.services.mongo_connector import sample_distinct_field_values

                return sample_distinct_field_values(connection, collection_name=entity_name, field_name=field_name, limit=limit)
            values = _sample_distinct_sql_values(connection, table_name=entity_name, column_name=field_name, limit=limit)
            if values is not None:
                return values
        except Exception:  # noqa: BLE001 — try the next connection rather than fail the whole check over one bad/unreachable one
            continue
    return None


def _fetch_sql_records(connection: DataConnection, *, table_name: str, column_names: list[str], limit: int) -> list[dict]:
    """Reflection-based select (see _sample_distinct_sql_values above for
    why not raw SQL) of just the columns a rule needs, for direct-connection
    execution — see direct_execution_service."""
    engine = _build_direct_engine(connection)
    try:
        metadata = MetaData()
        table = Table(table_name, metadata, autoload_with=engine)
        columns = [table.c[name] for name in column_names if name in table.c]
        if not columns:
            return []
        with engine.connect() as conn:
            rows = conn.execute(select(*columns).limit(limit))
            return [dict(row._mapping) for row in rows]
    finally:
        engine.dispose()


def fetch_direct_records(connection: DataConnection, *, entity_name: str, field_names: list[str], limit: int) -> list[dict]:
    """Dispatches to the right connector for a direct (non-Gateway)
    connection — the one place direct_execution_service needs to know
    MongoDB isn't reached the same way the SQL engines are."""
    connector = connectors.for_connection(connection)
    if connector is not None:
        return connector.fetch_records(connection, entity_name=entity_name, field_names=field_names, limit=limit)
    if connection.db_type == "mongodb":
        from app.services.mongo_connector import fetch_records as fetch_mongo_records

        return fetch_mongo_records(connection, collection_name=entity_name, field_paths=field_names, limit=limit)
    return _fetch_sql_records(connection, table_name=entity_name, column_names=field_names, limit=limit)


# ---- column value profiling (see app/core/value_profile.py) ----------------
_PROFILE_SAMPLE_ROWS = 500
_PROFILE_STATEMENT_TIMEOUT_MS = 15_000
# A data source can have hundreds of tables; profiling is background work and
# the tables that matter (the ones a control needs) are a small fraction.
_MAX_PROFILED_ENTITIES_PER_SOURCE = 400
_PROFILE_UPSERT_COLUMNS = (
    "sample_size", "null_ratio", "distinct_count", "distinct_ratio", "value_kind", "top_values", "max_length", "profiled_at",
)


_BINARY_TYPE_MARKERS = ("bytea", "blob", "binary", "image", "raw", "lob", "bfile")  # "lob" also covers Oracle/HANA CLOB and NCLOB: the driver returns locators, not text


def _sample_sql_rows(
    connection: DataConnection, *, table_name: str, columns: list[tuple[str, str | None]], limit: int
) -> list[dict]:
    """The first `limit` rows of the named (column, declared type) pairs — one
    query per table. Built from lightweight table()/column() constructs, so
    SQLAlchemy still quotes every identifier per dialect (names come from our
    own discovery metadata, never end-user input) but nothing is reflected:
    reflection is a dozen catalog round trips per table, which is what made
    profiling a remote database crawl. Best-effort guards this path adds
    because it reads tables nobody asked us to test yet: a statement timeout
    and a read-only transaction where the engine supports them, and no binary
    columns (never worth pulling over the wire). A guard a particular server
    rejects (e.g. a pooler that forbids SET) is skipped, not fatal."""
    wanted = [
        name for name, data_type in columns if not any(marker in (data_type or "").lower() for marker in _BINARY_TYPE_MARKERS)
    ]
    if not wanted:
        return []
    statement = select(*[sql_column(name) for name in wanted]).select_from(sql_table(table_name)).limit(limit)
    engine = _build_direct_engine(connection)
    try:
        with engine.connect() as conn:
            _apply_read_guards(conn, connection.db_type)
            return [dict(row._mapping) for row in conn.execute(statement)]
    finally:
        engine.dispose()


def _apply_read_guards(conn, db_type: str) -> None:
    """Best-effort statement timeout + read-only transaction for the reads this
    platform does on its own initiative (profiling, relationship inference). A
    guard a particular server rejects (e.g. a pooler that forbids SET) is
    skipped, not fatal."""
    try:
        if db_type == "postgresql":
            conn.execute(text(f"SET LOCAL statement_timeout = {_PROFILE_STATEMENT_TIMEOUT_MS}"))
            conn.execute(text("SET TRANSACTION READ ONLY"))
        elif db_type == "mysql":
            conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {_PROFILE_STATEMENT_TIMEOUT_MS}"))
        elif db_type == "oracle":
            # python-oracledb's per-call timeout (milliseconds); SET TRANSACTION must be the transaction's first statement.
            conn.connection.dbapi_connection.call_timeout = _PROFILE_STATEMENT_TIMEOUT_MS
            conn.execute(text("SET TRANSACTION READ ONLY"))
        elif db_type == "snowflake":
            conn.execute(text(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {_PROFILE_STATEMENT_TIMEOUT_MS // 1000}"))
    except Exception:  # noqa: BLE001
        conn.rollback()


def fetch_profile_rows(
    connection: DataConnection, *, entity_name: str, columns: list[tuple[str, str | None]], limit: int
) -> list[dict]:
    """Sampled rows for profiling, from whichever engine the connection is."""
    connector = connectors.for_connection(connection)
    if connector is not None:
        return connector.fetch_records(connection, entity_name=entity_name, field_names=[name for name, _ in columns], limit=limit)
    if connection.db_type == "mongodb":
        from app.services.mongo_connector import fetch_records as fetch_mongo_records

        return fetch_mongo_records(connection, collection_name=entity_name, field_paths=[name for name, _ in columns], limit=limit)
    return _sample_sql_rows(connection, table_name=entity_name, columns=columns, limit=limit)


def _store_profiles(db: Session, *, profiles: dict[uuid.UUID, ColumnProfile], stale_field_ids: list[uuid.UUID] | None = None) -> None:
    """Upserts profiles (and drops stale ones) in the caller's transaction;
    the caller commits. Shared by platform-side profiling and by a Gateway's
    reported profiles, so both paths store identically."""
    if stale_field_ids:
        db.execute(delete(DataFieldProfile).where(DataFieldProfile.field_id.in_(stale_field_ids)))
    if not profiles:
        return
    now = datetime.now(timezone.utc)
    stmt = pg_insert(DataFieldProfile).values(
        [
            {
                "field_id": field_id,
                "sample_size": p.sample_size,
                "null_ratio": p.null_ratio,
                "distinct_count": p.distinct_count,
                "distinct_ratio": p.distinct_ratio,
                "value_kind": p.value_kind,
                "top_values": list(p.top_values) if p.top_values is not None else None,
                "max_length": p.max_length,
                "profiled_at": now,
            }
            for field_id, p in profiles.items()
        ]
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[DataFieldProfile.field_id],
            set_={column: getattr(stmt.excluded, column) for column in _PROFILE_UPSERT_COLUMNS},
        )
    )


def _resolve(names: dict[str, object], wanted: str):
    """Exact match first, then case-insensitive (Oracle/HANA report upper-case
    catalog names for objects discovered in another case)."""
    if wanted in names:
        return names[wanted]
    lowered = {k.lower(): v for k, v in names.items()}
    return lowered.get(wanted.lower())


def _store_reported_schema_metadata(
    db: Session,
    *,
    data_source_id: uuid.UUID,
    constraints: dict[tuple[str, str], tuple],
    foreign_keys: dict[str, list],
) -> None:
    """Stores catalog facts (nullable/unique/indexed per column) and declared
    foreign keys from a discovery. Declared edges are refreshed from what the
    catalog now says, but a relationship an auditor confirmed or rejected, and
    every inferred edge, is left exactly as it is — discovery only speaks for
    what the schema declares."""
    entity_rows = db.execute(select(DataEntity.entity_id, DataEntity.entity_name).where(DataEntity.data_source_id == data_source_id)).all()
    entity_by_name = {name: entity_id for entity_id, name in entity_rows}
    field_rows = db.execute(
        select(DataField.field_id, DataField.field_name, DataField.entity_id).where(
            DataField.entity_id.in_(list(entity_by_name.values()))
        )
    ).all()
    fields: dict[uuid.UUID, dict[str, uuid.UUID]] = {}
    for field_id, field_name, entity_id in field_rows:
        fields.setdefault(entity_id, {})[field_name] = field_id
    name_by_entity = {entity_id: name for name, entity_id in entity_by_name.items()}

    rows = []
    for (entity_name, field_name), (nullable, unique, indexed) in constraints.items():
        entity_id = entity_by_name.get(entity_name)
        field_id = fields.get(entity_id, {}).get(field_name) if entity_id else None
        if field_id is not None:
            rows.append({"field_id": field_id, "is_nullable": nullable, "is_unique": bool(unique), "is_indexed": bool(indexed)})
    if rows:
        stmt = pg_insert(DataFieldConstraint).values(rows)
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=[DataFieldConstraint.field_id],
                set_={c: getattr(stmt.excluded, c) for c in ("is_nullable", "is_unique", "is_indexed")},
            )
        )

    if not foreign_keys:
        return
    unique_by_field = {
        field_id: is_unique
        for field_id, is_unique in db.execute(
            select(DataFieldConstraint.field_id, DataFieldConstraint.is_unique).where(
                DataFieldConstraint.field_id.in_([fid for per_entity in fields.values() for fid in per_entity.values()])
            )
        )
    }
    reported_entity_ids = [entity_by_name[name] for name in foreign_keys if name in entity_by_name]
    child_field_ids = [fid for eid in reported_entity_ids for fid in fields.get(eid, {}).values()]
    if child_field_ids:
        db.execute(
            delete(DataRelationship).where(
                DataRelationship.child_field_id.in_(child_field_ids),
                DataRelationship.kind == "declared_fk",
                DataRelationship.status == "detected",
            )
        )
    edges = []
    for entity_name, fks in foreign_keys.items():
        entity_id = entity_by_name.get(entity_name)
        if entity_id is None:
            continue
        for fk in fks:
            parent_entity_id = _resolve(entity_by_name, fk.referred_table)
            if parent_entity_id is None:
                continue  # the target lives outside this data source's discovered tables
            for child_col, parent_col in zip(fk.columns, fk.referred_columns):
                child_id = _resolve(fields.get(entity_id, {}), child_col)
                parent_id = _resolve(fields.get(parent_entity_id, {}), parent_col)
                if child_id is None or parent_id is None:
                    continue
                edges.append(
                    {
                        "data_source_id": data_source_id,
                        "child_field_id": child_id,
                        "parent_field_id": parent_id,
                        "kind": "declared_fk",
                        "constraint_name": fk.name,
                        "parent_unique": unique_by_field.get(parent_id),
                        "cardinality": "one_to_one" if unique_by_field.get(child_id) else "many_to_one",
                        "confidence": 100.0,
                        "evidence": {"source": "declared foreign key", "child_table": name_by_entity[entity_id]},
                    }
                )
    if edges:
        db.execute(pg_insert(DataRelationship).values(edges).on_conflict_do_nothing())


def _store_reported_profiles(db: Session, *, data_source_id: uuid.UUID, reported: dict[tuple[str, str], object]) -> None:
    """Screens and stores the column profiles a Gateway sent along with its
    discovery. Keyed (entity name, field name); a column the Gateway sent no
    profile for keeps whatever profile it already had."""
    rows = db.execute(
        select(DataField.field_id, DataField.field_name, DataField.is_sensitive, DataEntity.entity_name)
        .join(DataEntity, DataEntity.entity_id == DataField.entity_id)
        .where(DataEntity.data_source_id == data_source_id, DataEntity.entity_name.in_({k[0] for k in reported}))
    )
    profiles: dict[uuid.UUID, ColumnProfile] = {}
    for field_id, field_name, is_sensitive, entity_name in rows:
        raw = reported.get((entity_name, field_name))
        if raw is None:
            continue
        profile = sanitize_reported_profile(field_name, bool(is_sensitive), raw.model_dump())
        if profile is not None:
            profiles[field_id] = profile
    _store_profiles(db, profiles=profiles)


def _connected_direct_connections(db: Session, data_source_id: uuid.UUID) -> list[DataConnection]:
    return list(
        db.scalars(
            select(DataConnection).where(
                DataConnection.data_source_id == data_source_id,
                DataConnection.connection_mode == "direct",
                DataConnection.connection_status == "connected",
            )
        )
    )


def profile_entity_columns(
    db: Session, *, entity_id: uuid.UUID, connections: list[DataConnection] | None = None, sample_rows: int = _PROFILE_SAMPLE_ROWS
) -> int | None:
    """Samples one table over a direct connection and stores a profile per
    column (what it holds, never more than the privacy policy in
    build_column_profile allows). Returns how many columns were profiled, or
    None when it could not be done at all (no connected direct connection, or
    the read failed) — never an exception carrying driver text, which can
    echo the DSN. Gateway-only sources are not reachable from here."""
    entity = db.get(DataEntity, entity_id)
    if entity is None:
        return None
    fields = list(db.scalars(select(DataField).where(DataField.entity_id == entity_id)))
    if not fields:
        return 0
    if connections is None:
        connections = _connected_direct_connections(db, entity.data_source_id)

    rows: list[dict] | None = None
    for connection in connections:
        try:
            rows = fetch_profile_rows(
                connection, entity_name=entity.entity_name, columns=[(f.field_name, f.data_type) for f in fields], limit=sample_rows
            )
            break
        except Exception:  # noqa: BLE001 — try the next connection; the driver's text is not worth leaking
            logger.info("profiling %s failed on one connection", entity.entity_name)
            continue
    if rows is None:
        return None

    stale_ids = []
    profiles: dict[uuid.UUID, ColumnProfile] = {}
    # A column that was never actually read (binary, or absent from every
    # sampled document) must not be profiled as "all empty" — that would be a
    # false statement about the data, and could wrongly contradict a mapping.
    sampled_keys = set().union(*(row.keys() for row in rows)) if rows else set()
    for field in fields:
        if rows and field.field_name not in sampled_keys:
            continue
        profile = build_column_profile(
            field.field_name, [row.get(field.field_name) for row in rows], is_sensitive=field.is_sensitive
        )
        if profile is None:
            stale_ids.append(field.field_id)  # empty table: an old profile would now be a lie
        else:
            profiles[field.field_id] = profile
    _store_profiles(db, profiles=profiles, stale_field_ids=stale_ids)
    db.commit()
    return len(profiles)


# ---- relationship inference (see app/core/relationship_inference.py) --------
_MAX_RELATIONSHIP_CANDIDATES = 700
_MONGO_DISTINCT_CAP = 5000


def _text_cast_type(dialect_name: str):
    """The type a key column is cast to when compared with a differently-typed one. Oracle rejects a
    CAST(... AS VARCHAR2) with no length (ORA-00906) and HANA's bare NVARCHAR means length 1, so those two
    get an explicit one; the other engines keep the unbounded string they always had."""
    return String(4000) if dialect_name in ("oracle", "hana") else String


def _measure_sql(conn, candidate: Candidate) -> Measurement | None:
    """Exact containment of the child column's distinct values in the parent
    column, in ONE round trip: how many distinct child values, how many of
    them exist on the parent side, how many distinct parent values. Built from
    table()/column() constructs (identifiers quoted per dialect, nothing
    reflected). Differently-typed columns are compared as text, since a uuid
    against a varchar is an error on some engines."""
    child_col = sql_column(candidate.child.name)
    parent_col = sql_column(candidate.parent.name)
    child_t = sql_table(candidate.child.entity_name, child_col)
    parent_t = sql_table(candidate.parent.entity_name, parent_col)
    if (candidate.child.data_type or "") != (candidate.parent.data_type or ""):
        as_text = _text_cast_type(conn.dialect.name)
        child_expr, parent_expr = cast(child_col, as_text), cast(parent_col, as_text)
    else:
        child_expr, parent_expr = child_col, parent_col

    child_distinct = select(func.count(distinct(child_expr))).select_from(child_t).where(child_col.is_not(None)).scalar_subquery()
    matched = (
        select(func.count(distinct(child_expr)))
        .select_from(child_t)
        .where(child_col.is_not(None), child_expr.in_(select(parent_expr).select_from(parent_t).where(parent_col.is_not(None))))
        .scalar_subquery()
    )
    parent_distinct = select(func.count(distinct(parent_expr))).select_from(parent_t).where(parent_col.is_not(None)).scalar_subquery()
    parent_rows = select(func.count()).select_from(parent_t).where(parent_col.is_not(None)).scalar_subquery()
    row = conn.execute(select(child_distinct, matched, parent_distinct, parent_rows)).one()
    return Measurement(
        child_distinct=int(row[0]), matched_distinct=int(row[1]), parent_distinct=int(row[2]), parent_rows=int(row[3])
    )


def _measure_mongo(database, candidate: Candidate) -> Measurement | None:
    child_values = [v for v in database[candidate.child.entity_name].distinct(candidate.child.name) if v is not None]
    try:
        child_set = list({v for v in child_values})
    except TypeError:
        return None  # unhashable values (nested documents) — not a key column
    capped = len(child_set) > _MONGO_DISTINCT_CAP
    child_set = child_set[:_MONGO_DISTINCT_CAP]
    if not child_set:
        return Measurement(0, 0, 0)
    parent_collection = database[candidate.parent.entity_name]
    matched = parent_collection.distinct(candidate.parent.name, {candidate.parent.name: {"$in": child_set}})
    parent_distinct = len([v for v in parent_collection.distinct(candidate.parent.name) if v is not None])
    parent_rows = parent_collection.count_documents({candidate.parent.name: {"$ne": None}})
    return Measurement(
        child_distinct=len(child_set), matched_distinct=len({v for v in matched if v is not None}),
        parent_distinct=parent_distinct, capped=capped, parent_rows=parent_rows,
    )


def _measure_values(child_values: list, parent_values: list, *, as_text: bool, capped: bool) -> Measurement | None:
    """Containment of the child's distinct values in the parent's, over values already in memory (the
    file and API connections; the SQL and Mongo paths ask their own database). Differently-typed
    columns compare as text, exactly as _measure_sql does."""
    key = (lambda v: str(v)) if as_text else (lambda v: v)
    try:
        child_set = {key(v) for v in child_values if v is not None}
        parent_set = {key(v) for v in parent_values if v is not None}
    except TypeError:
        return None  # unhashable values (nested objects) — not a key column
    parent_rows = sum(1 for v in parent_values if v is not None)
    capped = capped or len(child_set) > _MONGO_DISTINCT_CAP
    if len(child_set) > _MONGO_DISTINCT_CAP:
        child_set = set(list(child_set)[:_MONGO_DISTINCT_CAP])
    return Measurement(
        child_distinct=len(child_set), matched_distinct=len(child_set & parent_set), parent_distinct=len(parent_set),
        capped=capped, parent_rows=parent_rows,
    )


def _measure_in_memory(connections: list[DataConnection], candidates: list[Candidate]) -> list[tuple[Candidate, Measurement]]:
    """Relationship measurement over rows read into memory: for file and API connections (there is no
    database to ask), and for pairs whose two tables live on DIFFERENT connections of one source (a
    database cannot join to another one). Each table is read ONCE, only the columns the candidates need
    (an API is not called again per pair), from the first connection that supplies it; a table nobody
    supplies, or that cannot be read, skips its pairs only. Rows cut off at the read cap make the result
    a lower bound, and it is marked so."""
    needed: dict[str, set[str]] = {}
    for c in candidates:
        needed.setdefault(c.child.entity_name, set()).add(c.child.name)
        needed.setdefault(c.parent.entity_name, set()).add(c.parent.name)
    rows_by_entity: dict[str, list[dict] | None] = {}
    for entity_name, columns in needed.items():
        rows_by_entity[entity_name] = None
        for connection in connections:
            try:
                rows_by_entity[entity_name] = fetch_profile_rows(
                    connection, entity_name=entity_name, columns=[(name, None) for name in sorted(columns)], limit=_CONNECTOR_ROW_CAP
                )
                break
            except Exception:  # noqa: BLE001 — not this connection's table, or unreadable: try the next
                continue
    out: list[tuple[Candidate, Measurement]] = []
    for candidate in candidates:
        child_rows = rows_by_entity.get(candidate.child.entity_name)
        parent_rows = rows_by_entity.get(candidate.parent.entity_name)
        if child_rows is None or parent_rows is None:
            continue
        measurement = _measure_values(
            [r.get(candidate.child.name) for r in child_rows],
            [r.get(candidate.parent.name) for r in parent_rows],
            as_text=(candidate.child.data_type or "") != (candidate.parent.data_type or ""),
            capped=len(child_rows) >= _CONNECTOR_ROW_CAP or len(parent_rows) >= _CONNECTOR_ROW_CAP,
        )
        if measurement is not None:
            out.append((candidate, measurement))
    return out


def _requirement_candidates(db: Session, data_source_id: uuid.UUID, columns: list[ColumnRef]) -> list[Candidate]:
    """Pairs a control's rule NEEDS joined, measured whatever the columns are
    called: `journal_entries.prepared_by` and `user.user_id` share no name, but
    GL-002 says they must relate, so that is exactly what to check. Both
    directions are measured, since which side is the referencing one is the
    data's answer, not ours."""
    import json

    from app.core.canonical_model import infer_object_for_entity
    from app.core.join_requirements import join_requirements_for, reference_roles_agree
    from app.core.join_resolution import candidate_columns
    from app.models.control_library import ControlRuleTemplate
    from app.models.risk_control import Control

    by_entity: dict[str, list[ColumnRef]] = {}
    for c in columns:
        by_entity.setdefault(c.entity_id, []).append(c)
    if not by_entity:
        return []

    rows = db.execute(
        select(ControlTableBinding.control_id, ControlTableBinding.canonical_table_name, ControlTableBinding.entity_id).where(
            ControlTableBinding.status == "bound",
            ControlTableBinding.entity_id.in_([uuid.UUID(e) for e in by_entity]),
        )
    ).all()
    objects_by_control: dict[uuid.UUID, dict[str, str]] = {}
    for control_id, table_name, entity_id in rows:
        objects_by_control.setdefault(control_id, {}).setdefault(infer_object_for_entity(table_name) or table_name, str(entity_id))
    if not objects_by_control:
        return []

    library_by_control = dict(
        db.execute(select(Control.control_id, Control.control_library_id).where(Control.control_id.in_(list(objects_by_control)))).all()
    )
    templates = {
        t.control_library_id: json.loads(t.rule_definition)
        for t in db.scalars(
            select(ControlRuleTemplate).where(ControlRuleTemplate.control_library_id.in_({v for v in library_by_control.values() if v}))
        )
    }

    seen: set[tuple[str, str]] = set()
    out: list[Candidate] = []
    for control_id, object_entity in objects_by_control.items():
        definition = templates.get(library_by_control.get(control_id))
        if not definition:
            continue
        for req in join_requirements_for(definition):
            left_entity, right_entity = object_entity.get(req.left_object), object_entity.get(req.right_object)
            if left_entity is None or right_entity is None or left_entity == right_entity:
                continue
            # A column that names a different actor (approved_by for a prepared_by
            # requirement) is the wrong pair however well its values match: never measured.
            lefts = [
                (c, n) for c, n in candidate_columns(req.left_object, req.left_field, by_entity.get(left_entity, []))
                if reference_roles_agree(req.left_field, c.name) != "mismatch"
            ]
            rights = [
                (c, n) for c, n in candidate_columns(req.right_object, req.right_field, by_entity.get(right_entity, []))
                if reference_roles_agree(req.right_field, c.name) != "mismatch"
            ]
            for lc, _ in lefts:
                for rc, _ in rights:
                    for child, parent in ((lc, rc), (rc, lc)):
                        key = (child.field_id, parent.field_id)
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append(
                            Candidate(child, parent, name_affinity=1.0, parent_unique=parent.is_primary_key or parent.is_unique, requirement_driven=True)
                        )
    return out


def _columns_for_inference(db: Session, data_source_id: uuid.UUID) -> list[ColumnRef]:
    rows = db.execute(
        select(DataField, DataEntity.entity_name, DataFieldConstraint, DataFieldProfile)
        .join(DataEntity, DataEntity.entity_id == DataField.entity_id)
        .outerjoin(DataFieldConstraint, DataFieldConstraint.field_id == DataField.field_id)
        .outerjoin(DataFieldProfile, DataFieldProfile.field_id == DataField.field_id)
        .where(DataEntity.data_source_id == data_source_id)
    )
    return [
        ColumnRef(
            field_id=str(field.field_id),
            entity_id=str(field.entity_id),
            entity_name=entity_name,
            name=field.field_name,
            data_type=field.data_type,
            is_primary_key=bool(field.is_primary_key),
            is_unique=bool(constraint.is_unique) if constraint is not None else False,
            distinct_ratio=profile.distinct_ratio if profile is not None else None,
            sample_size=profile.sample_size if profile is not None else 0,
            null_ratio=profile.null_ratio if profile is not None else None,
        )
        for field, entity_name, constraint, profile in rows
    ]


def build_inference_candidates(db: Session, data_source_id: uuid.UUID) -> list[Candidate]:
    """The column pairs worth measuring for one data source: the pairs a control's
    rule needs joined first (whatever the columns are called), then the
    name-driven ones, tables a control is bound to before the rest, capped.
    Database reads only: it needs no connection to the client's data, so the same
    list serves a direct connection (measured by the platform) and a Gateway
    (which measures inside the client's network and reports numbers back)."""
    columns = _columns_for_inference(db, data_source_id)
    candidates = generate_candidates(columns)
    driven = _requirement_candidates(db, data_source_id, columns)
    already = {(c.child.field_id, c.parent.field_id) for c in driven}
    candidates = driven + [c for c in candidates if (c.child.field_id, c.parent.field_id) not in already]
    if not candidates:
        return []

    bound = {
        str(entity_id)
        for entity_id in db.scalars(
            select(ControlTableBinding.entity_id).where(
                ControlTableBinding.entity_id.in_({uuid.UUID(c.child.entity_id) for c in candidates} | {uuid.UUID(c.parent.entity_id) for c in candidates})
            )
        )
        if entity_id is not None
    }
    candidates.sort(key=lambda c: (c.requirement_driven, (c.child.entity_id in bound) + (c.parent.entity_id in bound)), reverse=True)
    return candidates[:_MAX_RELATIONSHIP_CANDIDATES]


def store_inferred_edges(
    db: Session,
    data_source_id: uuid.UUID,
    measured: list[tuple[Candidate, Measurement]],
    *,
    scope: set[tuple[str, str]] | None = None,
) -> int:
    """Turns measurements into stored `inferred` edges. Only what the data
    supports is kept: the parent must be a key (declared, measured, or the
    control itself says so), and the containment worth recording.

    Replaces the previous inferred edges an auditor hasn't ruled on — every one
    for the source when `scope` is None (a full measurement), or only those for
    the pairs in `scope` (a Gateway reporting on the pairs it was asked about, so
    a partial or failed run never erases what an earlier run found). Declared
    foreign keys and anything confirmed or rejected are never touched. Commits."""
    edges = []
    for candidate, m in measured:
        if not (candidate.requirement_driven or candidate.parent_unique or m.parent_is_key):
            continue  # a parent that isn't a key on the client's data can't be referenced
        if not worth_storing(m, candidate.name_affinity):
            continue
        confidence, cardinality = score_inferred(candidate, m)
        edges.append(
            {
                "data_source_id": data_source_id,
                "child_field_id": uuid.UUID(candidate.child.field_id),
                "parent_field_id": uuid.UUID(candidate.parent.field_id),
                "kind": "inferred",
                "containment": round(m.containment * 100, 2),
                "child_distinct": m.child_distinct,
                "parent_distinct": m.parent_distinct,
                "parent_unique": candidate.parent_unique or m.parent_is_key,
                "cardinality": cardinality,
                "confidence": confidence,
                "evidence": {
                    "source": "value containment measured on the client's data",
                    "matched_distinct": m.matched_distinct,
                    "name_affinity": candidate.name_affinity,
                    "capped": m.capped,
                },
            }
        )
    stale = delete(DataRelationship).where(
        DataRelationship.data_source_id == data_source_id,
        DataRelationship.kind == "inferred",
        DataRelationship.status == "detected",
    )
    if scope is not None:
        pairs = [(uuid.UUID(c), uuid.UUID(p)) for c, p in scope]
        if pairs:
            stale = stale.where(tuple_(DataRelationship.child_field_id, DataRelationship.parent_field_id).in_(pairs))
        else:
            stale = None
    if stale is not None:
        db.execute(stale)
    if edges:
        db.execute(pg_insert(DataRelationship).values(edges).on_conflict_do_nothing())
    db.commit()
    return len(edges)


def infer_relationships_for_source(db: Session, *, data_source_id: uuid.UUID) -> int | None:
    """Measures the plausible undeclared relationships between this source's
    tables on the client's own data and stores the ones the data supports (see
    app/core/relationship_inference.py). Only direct connections can be reached
    from here (a Gateway measures for itself, see gateway_relationship_service);
    None means it could not be done at all."""
    connections = _connected_direct_connections(db, data_source_id)
    if not connections:
        return None
    candidates = build_inference_candidates(db, data_source_id)
    if not candidates:
        return 0

    # Measuring the client's database can take minutes. End the read transaction
    # first: a session left idle that long has its connection dropped by the
    # database (Neon does), and the write below would fail on it.
    # Detach the connection objects first: touching an expired one after the
    # commit would silently start a new transaction and hold it open again.
    for connection in connections:
        try:
            db.expunge(connection)
        except Exception:  # noqa: BLE001 — already detached (or not a session-managed object)
            pass
    db.commit()
    # Every connection measures the pairs it can reach; the tables are pooled under the source, so a
    # file connection and a database connection each supply some of them. A pair measured by an
    # earlier connection is not measured again.
    measured_by_pair: dict[Candidate, Measurement] = {}
    any_connection_worked = False
    for connection in connections:
        remaining = [c for c in candidates if c not in measured_by_pair]
        if not remaining:
            break
        try:
            for candidate, measurement in _measure_all(connection, remaining):
                measured_by_pair.setdefault(candidate, measurement)
            any_connection_worked = True
        except Exception:  # noqa: BLE001 — try the next connection; the driver's text is not worth leaking
            logger.info("relationship inference failed on one connection")
            continue
    remaining = [c for c in candidates if c not in measured_by_pair]
    if remaining and len(connections) > 1 and any(connectors.for_connection(c) is not None for c in connections):
        # What is left are pairs whose tables are on different connections (a database cannot join to
        # another one). With a file or API connection involved the key columns can be read and compared
        # in memory, so a payroll file and an HR file uploaded separately are still related.
        try:
            for candidate, measurement in _measure_in_memory(connections, remaining):
                measured_by_pair.setdefault(candidate, measurement)
            any_connection_worked = True
        except Exception:  # noqa: BLE001
            logger.info("cross-connection relationship inference failed")
    if not any_connection_worked:
        return None
    return store_inferred_edges(db, data_source_id, list(measured_by_pair.items()))


def _measure_all(connection: DataConnection, candidates: list[Candidate]) -> list[tuple[Candidate, Measurement]]:
    """One connection for the whole run; a pair that errors (an incomparable
    type, a permission gap) is skipped, not fatal."""
    out: list[tuple[Candidate, Measurement]] = []
    if connectors.for_connection(connection) is not None:
        return _measure_in_memory([connection], candidates)
    if connection.db_type == "mongodb":
        from app.services.mongo_connector import _build_mongo_client

        client = _build_mongo_client(connection, password=decrypt_secret(connection.encrypted_password))
        try:
            database = client[connection.database_name]
            for candidate in candidates:
                try:
                    m = _measure_mongo(database, candidate)
                except Exception:  # noqa: BLE001
                    continue
                if m is not None:
                    out.append((candidate, m))
        finally:
            client.close()
        return out

    engine = _build_direct_engine(connection)
    try:
        for candidate in candidates:
            try:
                with engine.connect() as conn:
                    _apply_read_guards(conn, connection.db_type)
                    m = _measure_sql(conn, candidate)
            except Exception:  # noqa: BLE001 — e.g. a type the engine can't compare; skip this pair only
                continue
            if m is not None:
                out.append((candidate, m))
    finally:
        engine.dispose()
    return out


_background_lock = threading.Lock()
_background_running: set[uuid.UUID] = set()


@contextmanager
def _one_background_job_per_source(data_source_id: uuid.UUID):
    """Yields True if this call may run, False if a profiling or relationship job for
    the same data source is already running. Each job reads a whole source for
    minutes, so repeated "Discover schema" / "Detect relationships" clicks must not
    stack up overlapping jobs (or have two inference runs delete each other's edges)."""
    with _background_lock:
        if data_source_id in _background_running:
            allowed = False
        else:
            _background_running.add(data_source_id)
            allowed = True
    try:
        yield allowed
    finally:
        if allowed:
            with _background_lock:
                _background_running.discard(data_source_id)


def profile_data_source_in_background(data_source_id: uuid.UUID) -> None:
    """Profiles every table of a data source, one at a time, in its own
    database session — meant for a daemon thread started after discovery (or
    on demand), so a large source never holds up a request. One table's
    failure never stops the rest."""
    from app.db.session import SessionLocal

    with _one_background_job_per_source(data_source_id) as allowed:
        if not allowed:
            logger.info("a background job for data source %s is already running; not starting another", data_source_id)
            return
        _profile_data_source(SessionLocal, data_source_id)


def _profile_data_source(SessionLocal, data_source_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        connections = _connected_direct_connections(db, data_source_id)
        if not connections:
            return
        entity_ids = list(db.scalars(select(DataEntity.entity_id).where(DataEntity.data_source_id == data_source_id)))
        # Tables a control is already bound to are the ones whose fit is being
        # judged right now — profile those first, then the rest up to the cap.
        bound = set(
            db.scalars(select(ControlTableBinding.entity_id).where(ControlTableBinding.entity_id.in_(entity_ids)))
        ) if entity_ids else set()
        entity_ids = sorted(entity_ids, key=lambda entity_id: entity_id not in bound)[:_MAX_PROFILED_ENTITIES_PER_SOURCE]
        for entity_id in entity_ids:
            try:
                profile_entity_columns(db, entity_id=entity_id, connections=connections)
            except Exception:  # noqa: BLE001
                db.rollback()
                logger.exception("profiling entity %s failed", entity_id)
        # Profiles say which columns look like keys, so relationships come last.
        try:
            infer_relationships_for_source(db, data_source_id=data_source_id)
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("relationship inference for source %s failed", data_source_id)
    finally:
        db.close()


def infer_relationships_in_background(data_source_id: uuid.UUID) -> None:
    """Relationship inference only (profiles already exist), for a refresh."""
    from app.db.session import SessionLocal

    with _one_background_job_per_source(data_source_id) as allowed:
        if not allowed:
            logger.info("a background job for data source %s is already running; not starting another", data_source_id)
            return
        db = SessionLocal()
        try:
            infer_relationships_for_source(db, data_source_id=data_source_id)
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("relationship inference for source %s failed", data_source_id)
        finally:
            db.close()


def list_entities_for_organization(db: Session, *, organization_id: uuid.UUID) -> list[DataEntity]:
    return list(
        db.scalars(
            select(DataEntity)
            .join(DataSource, DataSource.data_source_id == DataEntity.data_source_id)
            .where(DataSource.organization_id == organization_id)
        )
    )
