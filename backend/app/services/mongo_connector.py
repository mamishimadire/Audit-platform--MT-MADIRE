"""
MongoDB direct-connection support — deliberately NOT routed through the
generic SQLAlchemy `inspect(engine)` path every other direct connector in
data_source_service.py shares (see its module-level comments). MongoDB has
no fixed table/column shape to inspect:

    Database -> Collection -> Document -> Fields

so "schema discovery" here means something different: list collections,
sample a bounded number of documents from each, and infer what fields
*tend* to look like from what was actually observed — never presented as a
guaranteed structure. Two documents in the same collection can legally have
completely different fields; this module surfaces that (optional/mixed-type
fields) rather than hiding it.

Despite the different discovery mechanics, this reuses everything else the
other connectors already have: the same credential encryption
(app.core.crypto), the same DiscoveredEntity/DiscoveredField output shape
consumed by data_source_service.replace_discovery (so table binding, column
mapping, mapping approval, and audit test configuration all work against a
MongoDB collection exactly as they do against a SQL table — no separate
code path for any of that), and the same "never leak a raw driver
exception with the DSN/credentials in it" principle as the SQL test path.
"""
from datetime import datetime

from bson import Binary, ObjectId
from bson.decimal128 import Decimal128
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.core.crypto import decrypt_secret
from app.models.data_source import DataConnection
from app.schemas.data_source import DiscoveredEntity, DiscoveredField

_CONNECT_TIMEOUT_MS = 8000

# "Approximately 100 documents" per the build spec — large enough to catch
# most field variation in a typical collection, small enough that discovery
# stays fast even against a big collection (an aggregation $sample stage
# is a bounded-cost random sample, not a full collection scan).
SAMPLE_SIZE = 100


def _build_mongo_client(connection: DataConnection, *, password: str) -> MongoClient:
    # Credentials are passed as separate kwargs, never concatenated into the
    # URI string — pymongo handles the escaping itself, so a password
    # containing '@', ':' or '%' can't break the connection string (same
    # principle data_source_service._direct_engine_url applies via
    # SQLAlchemy's URL.create for the SQL engines).
    scheme = "mongodb+srv" if connection.mongodb_srv else "mongodb"
    host_part = connection.host if connection.mongodb_srv else f"{connection.host}:{connection.port}"
    uri = f"{scheme}://{host_part}/{connection.database_name}"
    return MongoClient(
        uri,
        username=connection.username,
        password=password,
        serverSelectionTimeoutMS=_CONNECT_TIMEOUT_MS,
        connectTimeoutMS=_CONNECT_TIMEOUT_MS,
    )


def test_mongo_connection(connection: DataConnection) -> tuple[bool, str]:
    password = decrypt_secret(connection.encrypted_password)
    client = None
    try:
        client = _build_mongo_client(connection, password=password)
        client.admin.command("ping")
        return True, "Connected successfully."
    except ValueError as exc:
        return False, str(exc)  # our own decrypt-failure message — safe to show
    except PyMongoError:
        # pymongo exceptions can include resolved hostnames — never the
        # password (passed separately, never interpolated into the URI) —
        # but stay generic anyway, same as every other engine's catch-all.
        return False, "Could not connect — check the host, credentials, and that this address is reachable from the platform."
    finally:
        if client is not None:
            client.close()


def _bson_type_label(value: object) -> str | None:
    """None means 'this value tells us nothing about the field's type' —
    either the value itself is null, or it's a non-empty nested object
    (which _flatten already descended into, so it's never typed directly
    here as a leaf)."""
    if value is None:
        return None
    if isinstance(value, bool):  # must precede int — bool is an int subclass
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "double"
    if isinstance(value, Decimal128):
        return "decimal"
    if isinstance(value, str):
        return "string"
    if isinstance(value, datetime):
        return "date"
    if isinstance(value, ObjectId):
        return "objectid"
    if isinstance(value, (bytes, Binary)):
        return "binary"
    if isinstance(value, list):
        return "array"  # element types aren't flattened further — see module docstring
    if isinstance(value, dict):
        return "object" if not value else None  # an empty {} has no sub-fields to flatten, so it's its own leaf
    return type(value).__name__


def _flatten(doc: dict, prefix: str = "") -> dict[str, object]:
    """One document's fields as {dotted.path: value} — e.g.
    {"employee": {"department": {"name": "Finance"}}} becomes
    {"employee.department.name": "Finance"}. This exact convention is what
    field mapping, mapping approval, and audit test configuration consume
    downstream, so it must never diverge between discovery and execution."""
    flat: dict[str, object] = {}
    for key, value in doc.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


def _index_facts(index_info: dict | None) -> tuple[set[str], set[str]] | None:
    """(unique single-field paths, indexed leading paths) from pymongo's
    index_information(). None when indexes could not be read."""
    if index_info is None:
        return None
    unique: set[str] = set()
    indexed: set[str] = set()
    for name, info in index_info.items():
        keys = [k for k, _direction in info.get("key", [])]
        if not keys:
            continue
        indexed.add(keys[0])
        if (info.get("unique") or name == "_id_") and len(keys) == 1:
            unique.add(keys[0])
    return unique, indexed


def _discover_collection(name: str, sampled_docs: list[dict], index_info: dict | None = None) -> DiscoveredEntity:
    index_facts = _index_facts(index_info)
    total = len(sampled_docs)
    presence: dict[str, int] = {}
    types_seen: dict[str, set[str]] = {}
    order: list[str] = []  # first-seen order reads more naturally than alphabetical

    for doc in sampled_docs:
        for path, value in _flatten(doc).items():
            if path not in presence:
                presence[path] = 0
                types_seen[path] = set()
                order.append(path)
            presence[path] += 1
            label = _bson_type_label(value)
            if label is not None:
                types_seen[path].add(label)

    fields = []
    for path in order:
        labels = sorted(types_seen[path])
        if not labels:
            type_text = "null"
        elif len(labels) == 1:
            type_text = labels[0]
        else:
            # Inconsistent across the sample — e.g. one document has a
            # string in this field, another an integer. Surfaced plainly
            # rather than guessing one and hiding the disagreement.
            type_text = "mixed: " + "/".join(labels)
        if presence[path] < total:
            type_text += " (optional)"
        fields.append(
            DiscoveredField(
                field_name=path,
                data_type=type_text,
                is_primary_key=(path == "_id"),
                # A field missing from some sampled documents is nullable; otherwise Mongo doesn't say.
                is_nullable=True if presence[path] < total else None,
                is_unique=(path in index_facts[0]) if index_facts is not None else None,
                is_indexed=(path in index_facts[1]) if index_facts is not None else None,
            )
        )

    description = (
        f"Inferred from {total} sampled document(s) — other documents in this collection may have additional, "
        "missing, or differently-typed fields."
        if total
        else "Empty collection — no documents to sample."
    )
    return DiscoveredEntity(entity_name=name, entity_type="collection", description=description, fields=fields)


def sample_distinct_field_values(connection: DataConnection, *, collection_name: str, field_name: str, limit: int = 500) -> set[str]:
    """Used for relationship validation (does a mapped join field on this
    side actually share values with the other side?), not discovery.
    `.distinct()` has no server-side LIMIT of its own — capped after
    fetching, which is fine at audit-data scale but would need a real
    aggregation-pipeline $group+$limit approach against a collection with
    an enormous number of distinct values."""
    password = decrypt_secret(connection.encrypted_password)
    client = _build_mongo_client(connection, password=password)
    try:
        database = client[connection.database_name]
        values = database[collection_name].distinct(field_name)
        return {str(v) for v in values[:limit] if v is not None}
    finally:
        client.close()


def fetch_records(connection: DataConnection, *, collection_name: str, field_paths: list[str], limit: int) -> list[dict]:
    """Full documents (not a distinct-value sample) for direct-connection
    rule execution — see direct_execution_service. Projects only the fields
    the rule actually needs and flattens with the same dotted-path
    convention _discover_collection/_flatten use, so a mapped canonical
    field resolves to the same key here as it did during discovery."""
    password = decrypt_secret(connection.encrypted_password)
    client = _build_mongo_client(connection, password=password)
    try:
        database = client[connection.database_name]
        projection = {path: 1 for path in field_paths}
        if "_id" not in field_paths:
            projection["_id"] = 0
        cursor = database[collection_name].find({}, projection).limit(limit)
        return [_flatten(doc) for doc in cursor]
    finally:
        client.close()


def discover_mongo_schema(connection: DataConnection) -> list[DiscoveredEntity]:
    password = decrypt_secret(connection.encrypted_password)
    client = _build_mongo_client(connection, password=password)
    try:
        database = client[connection.database_name]
        entities = []
        for collection_name in database.list_collection_names():
            sampled = list(database[collection_name].aggregate([{"$sample": {"size": SAMPLE_SIZE}}]))
            try:
                index_info = database[collection_name].index_information()
            except Exception:  # noqa: BLE001 — indexes are a bonus; never fail discovery over them
                index_info = None
            entities.append(_discover_collection(collection_name, sampled, index_info))
        return entities
    finally:
        client.close()
