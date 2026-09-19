"""
MongoDB connector — deliberately NOT a SqlAlchemyConnector subclass (see
base.py's module docstring): MongoDB has no fixed table/column shape to
reflect, so "discovery" here means something different — list collections,
sample a bounded number of documents from each, and infer what fields
*tend* to look like from what was actually observed, never presented as a
guaranteed structure. Two documents in the same collection can legally have
completely different fields; this surfaces that (optional/mixed-type
fields) rather than hiding it.

This is a straight port of app/services/mongo_connector.py on the platform
side (the direct-connection MongoDB path) to this package's connector
interface — same sampling size, same dotted-path flattening convention, same
BSON-type-label inference — so a collection maps to identical fields
whether it's reached via a direct connection or through this Gateway. Kept
as a deliberate duplicate rather than a shared import: this package is
deployed independently of the backend and cannot import from it.

Exists specifically so a client whose MongoDB is NOT reachable from the
public internet (self-hosted, on-premises, inside a private network) still
gets the same "install the Gateway, point it at your database, done"
experience as a MySQL/Postgres/SQL Server client — no IP allowlisting, no
inbound firewall rule, since the Gateway reaches the database from INSIDE
that network and only ever calls OUT to the platform. A MongoDB Atlas
(cloud-hosted) database can use either this or a direct connection with an
IP allowlist — both are legitimate; this is the one that needs zero client
network configuration at all.
"""
from datetime import datetime

from bson import Binary, ObjectId
from bson.decimal128 import Decimal128
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from gateway.connectors.base import ConnectorConfig

_CONNECT_TIMEOUT_MS = 8000

# Same "approximately 100 documents" sampling the platform's own direct
# MongoDB path uses — large enough to catch most field variation in a
# typical collection, small enough that discovery stays fast even against a
# big one (an aggregation $sample stage is a bounded-cost random sample,
# not a full collection scan).
SAMPLE_SIZE = 100


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
        return "array"  # element types aren't flattened further
    if isinstance(value, dict):
        return "object" if not value else None  # an empty {} has no sub-fields to flatten, so it's its own leaf
    return type(value).__name__


def _flatten(doc: dict, prefix: str = "") -> dict[str, object]:
    """One document's fields as {dotted.path: value} — e.g.
    {"employee": {"department": {"name": "Finance"}}} becomes
    {"employee.department.name": "Finance"}. Must stay identical to the
    platform's own mongo_connector._flatten — a mapped canonical field has
    to resolve to the same key at discovery time and at execution time."""
    flat: dict[str, object] = {}
    for key, value in doc.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


class MongoConnector:
    def __init__(self, config: ConnectorConfig):
        self.config = config
        self._client: MongoClient | None = None

    def _client_for(self) -> MongoClient:
        if self._client is None:
            c = self.config
            # Credentials are passed as separate kwargs, never concatenated
            # into the URI string — pymongo handles the escaping itself, so
            # a password containing '@', ':' or '%' can't break the
            # connection string.
            scheme = "mongodb+srv" if c.mongodb_srv else "mongodb"
            host_part = c.host if c.mongodb_srv else f"{c.host}:{c.port}"
            uri = f"{scheme}://{host_part}/{c.database}"
            self._client = MongoClient(
                uri,
                username=c.username,
                password=c.password,
                serverSelectionTimeoutMS=_CONNECT_TIMEOUT_MS,
                connectTimeoutMS=_CONNECT_TIMEOUT_MS,
            )
        return self._client

    def test_connection(self) -> tuple[bool, str | None]:
        try:
            self._client_for().admin.command("ping")
            return True, "Connected successfully."
        except PyMongoError as exc:
            # A raw pymongo exception can include resolved hostnames — never
            # the password (passed separately, never interpolated into the
            # URI) — but stays generic anyway, same as every SQL connector's
            # catch-all in base.py.
            return False, f"Could not connect — check the host, credentials, and that this address is reachable: {exc}"

    def discover(self) -> list[dict]:
        database = self._client_for()[self.config.database]
        entities: list[dict] = []
        for collection_name in database.list_collection_names():
            sampled = list(database[collection_name].aggregate([{"$sample": {"size": SAMPLE_SIZE}}]))
            try:
                index_info = database[collection_name].index_information()
            except Exception:  # noqa: BLE001 — indexes are a bonus; never fail discovery over them
                index_info = None
            entities.append(self._discover_collection(collection_name, sampled, index_info))
        return entities

    @staticmethod
    def _index_facts(index_info: dict | None):
        """(unique single-field paths, indexed leading paths); None if indexes could not be read."""
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

    def _discover_collection(self, name: str, sampled_docs: list[dict], index_info: dict | None = None) -> dict:
        index_facts = self._index_facts(index_info)
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
                # Inconsistent across the sample — surfaced plainly rather
                # than guessing one and hiding the disagreement.
                type_text = "mixed: " + "/".join(labels)
            if presence[path] < total:
                type_text += " (optional)"
            fields.append(
                {
                    "field_name": path,
                    "data_type": type_text,
                    "is_primary_key": path == "_id",
                    "is_sensitive": False,  # sensitivity is an auditor judgement call, not auto-detected
                    "is_nullable": True if presence[path] < total else None,
                    "is_unique": (path in index_facts[0]) if index_facts is not None else None,
                    "is_indexed": (path in index_facts[1]) if index_facts is not None else None,
                }
            )

        description = (
            f"Inferred from {total} sampled document(s) — other documents in this collection may have additional, "
            "missing, or differently-typed fields."
            if total
            else "Empty collection — no documents to sample."
        )
        return {"entity_name": name, "entity_type": "collection", "description": description, "fields": fields}

    def fetch_dataframe(self, entity_name: str, columns: list[str]):
        import pandas as pd

        database = self._client_for()[self.config.database]
        projection = {path: 1 for path in columns}
        if "_id" not in columns:
            projection["_id"] = 0
        cursor = database[entity_name].find({}, projection)
        rows = [_flatten(doc) for doc in cursor]
        return pd.DataFrame(rows, columns=columns)

    def sample_rows(self, entity_name: str, columns: list[tuple[str, str | None]], limit: int) -> list[dict]:
        """Up to `limit` documents, flattened to the same dotted paths
        discovery reports, for column profiling (see gateway/profiling.py)."""
        database = self._client_for()[self.config.database]
        paths = [name for name, _ in columns]
        projection = {path: 1 for path in paths}
        if "_id" not in paths:
            projection["_id"] = 0
        return [_flatten(doc) for doc in database[entity_name].find({}, projection).limit(limit)]

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
