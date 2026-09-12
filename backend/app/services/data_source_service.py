import uuid
from datetime import datetime, timezone

from sqlalchemy import MetaData, Table, create_engine, delete, distinct, insert, inspect, select, update
from sqlalchemy.engine import Engine, URL
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.data_source import DataConnection, DataEntity, DataField, DataSource
from app.schemas.data_source import (
    DataConnectionCreate,
    DataSourceCreate,
    DirectConnectionCreate,
    DiscoveredEntity,
    DiscoveredField,
    DiscoveryPayload,
)
from app.services.audit_log_service import log_action

# Short, fixed connect timeout regardless of DB type — a direct connection
# targets a host/port the caller supplies, so a slow or unreachable target
# must fail fast rather than tying up a backend worker indefinitely.
_CONNECT_TIMEOUT_SECONDS = 8

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


def _build_direct_engine(connection: DataConnection) -> Engine:
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


def test_direct_connection(db: Session, *, connection: DataConnection) -> tuple[bool, str]:
    """Actually connects to the target database — the only way to know a
    direct (non-Gateway) connection works, since there's no Gateway to ask."""
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
    if connection.db_type == "mongodb":
        from app.services.mongo_connector import discover_mongo_schema

        entities = discover_mongo_schema(connection)
        return replace_discovery(db, data_source_id=connection.data_source_id, payload=DiscoveryPayload(entities=entities))

    engine = _build_direct_engine(connection)
    try:
        inspector = inspect(engine)
        entities: list[DiscoveredEntity] = []
        for table_name in inspector.get_table_names():
            pk_columns = set(inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
            fields = [
                DiscoveredField(
                    field_name=col["name"],
                    data_type=str(col.get("type")),
                    is_primary_key=col["name"] in pk_columns,
                )
                for col in inspector.get_columns(table_name)
            ]
            entities.append(DiscoveredEntity(entity_name=table_name, entity_type="table", fields=fields))
    finally:
        engine.dispose()

    return replace_discovery(db, data_source_id=connection.data_source_id, payload=DiscoveryPayload(entities=entities))


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


def replace_discovery(db: Session, *, data_source_id: uuid.UUID, payload: DiscoveryPayload) -> list[DataEntity]:
    """
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

    seen_entity_names: set[str] = set()
    new_entity_rows = []
    new_field_rows = []
    stale_field_ids: list[uuid.UUID] = []
    result_entity_ids: list[uuid.UUID] = []

    for discovered in payload.entities:
        seen_entity_names.add(discovered.entity_name)
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

    stale_entity_ids = [e.entity_id for name, e in existing_entities.items() if name not in seen_entity_names]

    if stale_field_ids:
        db.execute(delete(DataField).where(DataField.field_id.in_(stale_field_ids)))
    if stale_entity_ids:
        db.execute(delete(DataEntity).where(DataEntity.entity_id.in_(stale_entity_ids)))
    if new_entity_rows:
        db.execute(insert(DataEntity), new_entity_rows)
    if new_field_rows:
        db.execute(insert(DataField), new_field_rows)

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
            if connection.db_type == "mongodb":
                from app.services.mongo_connector import sample_distinct_field_values

                return sample_distinct_field_values(connection, collection_name=entity_name, field_name=field_name, limit=limit)
            values = _sample_distinct_sql_values(connection, table_name=entity_name, column_name=field_name, limit=limit)
            if values is not None:
                return values
        except Exception:  # noqa: BLE001 — try the next connection rather than fail the whole check over one bad/unreachable one
            continue
    return None


def list_entities_for_organization(db: Session, *, organization_id: uuid.UUID) -> list[DataEntity]:
    return list(
        db.scalars(
            select(DataEntity)
            .join(DataSource, DataSource.data_source_id == DataEntity.data_source_id)
            .where(DataSource.organization_id == organization_id)
        )
    )
