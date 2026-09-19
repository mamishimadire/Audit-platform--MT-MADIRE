"""
Measuring how the client's tables relate, inside the client's network.

The platform asks (relationship-requests) which column pairs are worth checking; this
counts, per pair, how many DISTINCT child values there are and how many of them also
exist on the parent side, on the client's own database. Only those counts go back to
the platform: no value, no row, no column content ever leaves this machine.

Deliberate duplicate of the platform's _measure_sql / _measure_mongo in
app/services/data_source_service.py (this package is deployed independently and cannot
import from the backend). The backend's test suite runs both implementations on the same
database and fails if they ever disagree: change them together.

Depends only on SQLAlchemy Core (pymongo is imported lazily for MongoDB), so that test
can load it without the Gateway's other dependencies.

Turn it off with `relationships_enabled: false` in config.yaml, or for one connection with
`relationships: false`.
"""
import logging

from sqlalchemy import String, cast, distinct, func, select
from sqlalchemy import column as sql_column
from sqlalchemy import table as sql_table

logger = logging.getLogger("gateway.relationships")

_MONGO_DISTINCT_CAP = 5000


def measure_sql(conn, pair: dict, schema: str | None = None) -> dict | None:
    """Exact containment of the child column's distinct values in the parent column, in
    ONE round trip. Built from table()/column() constructs (identifiers quoted per
    dialect, nothing reflected). Differently-typed columns are compared as text, since a
    uuid against a varchar is an error on some engines."""
    child_col = sql_column(pair["child_column"])
    parent_col = sql_column(pair["parent_column"])
    child_t = sql_table(pair["child_entity"], child_col, schema=schema)
    parent_t = sql_table(pair["parent_entity"], parent_col, schema=schema)
    if (pair.get("child_type") or "") != (pair.get("parent_type") or ""):
        child_expr, parent_expr = cast(child_col, String), cast(parent_col, String)
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
    return _result(pair, int(row[0]), int(row[1]), int(row[2]), int(row[3]), capped=False)


def measure_mongo(database, pair: dict) -> dict | None:
    child_values = [v for v in database[pair["child_entity"]].distinct(pair["child_column"]) if v is not None]
    try:
        child_set = list({v for v in child_values})
    except TypeError:
        return None  # unhashable values (nested documents): not a key column
    capped = len(child_set) > _MONGO_DISTINCT_CAP
    child_set = child_set[:_MONGO_DISTINCT_CAP]
    if not child_set:
        return _result(pair, 0, 0, 0, 0, capped=False)
    parent_collection = database[pair["parent_entity"]]
    matched = parent_collection.distinct(pair["parent_column"], {pair["parent_column"]: {"$in": child_set}})
    parent_distinct = len([v for v in parent_collection.distinct(pair["parent_column"]) if v is not None])
    parent_rows = parent_collection.count_documents({pair["parent_column"]: {"$ne": None}})
    return _result(pair, len(child_set), len({v for v in matched if v is not None}), parent_distinct, parent_rows, capped=capped)


def _result(pair: dict, child_distinct: int, matched: int, parent_distinct: int, parent_rows: int, *, capped: bool) -> dict:
    """The wire shape of one measurement: counts, and the ids the platform gave the pair."""
    return {
        "child_field_id": pair["child_field_id"],
        "parent_field_id": pair["parent_field_id"],
        "child_distinct": child_distinct,
        "matched_distinct": matched,
        "parent_distinct": parent_distinct,
        "parent_rows": parent_rows,
        "capped": capped,
    }


def measure_all(measure_one, pairs: list[dict]) -> list[dict]:
    """Runs measure_one over every requested pair. One pair failing (an incomparable type,
    a table this Gateway's database doesn't have, a permission gap) never stops the rest,
    and its reason (which can echo connection details) is not logged in full."""
    results = []
    for pair in pairs:
        try:
            result = measure_one(pair)
        except Exception:  # noqa: BLE001
            logger.info("Could not measure %s.%s -> %s.%s", pair.get("child_entity"), pair.get("child_column"), pair.get("parent_entity"), pair.get("parent_column"))
            continue
        if result is not None:
            results.append(result)
    return results
