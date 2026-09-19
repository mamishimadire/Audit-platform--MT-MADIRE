"""
Turns what a database's catalog says about a table (primary key, unique
constraints, indexes, foreign keys) into the flat per-column facts and
relationship edges the platform stores.

Pure functions over the plain dicts SQLAlchemy's inspector returns. This is a
deliberate, dependency-free copy of the platform's app/core/schema_metadata.py
(this package is deployed independently and cannot import from the backend).
The backend's test suite runs both on the same inputs and fails if they ever
disagree — change them together.

None means "the catalog didn't say" (a dialect that can't report it, or a call
that failed), never "no": Snowflake and SAP HANA rarely declare foreign keys,
and MongoDB has none at all, so absence of a declared relationship is not
evidence there is no relationship.
"""
from __future__ import annotations

from typing import Iterable


def derive_column_constraints(
    column_names: Iterable[str],
    *,
    nullable_by_column: dict[str, bool | None] | None = None,
    pk_columns: Iterable[str] = (),
    unique_constraints: list[dict] | None = None,
    indexes: list[dict] | None = None,
) -> dict[str, dict]:
    """{column: {is_nullable, is_unique, is_indexed}}.

    A column is unique when it is the ONLY column of a unique constraint, a
    unique index, or the primary key (a member of a composite key is not, on
    its own, unique). It is indexed when it leads any index or key. A key
    column is never nullable."""
    pk = list(pk_columns)
    unique_cols: set[str] = set()
    indexed_cols: set[str] = set()

    if len(pk) == 1:
        unique_cols.add(pk[0])
    if pk:
        indexed_cols.add(pk[0])
    for constraint in unique_constraints or []:
        cols = list(constraint.get("column_names") or [])
        if len(cols) == 1:
            unique_cols.add(cols[0])
        if cols:
            indexed_cols.add(cols[0])
    for index in indexes or []:
        cols = [c for c in (index.get("column_names") or []) if c]
        if cols:
            indexed_cols.add(cols[0])
            if index.get("unique") and len(cols) == 1:
                unique_cols.add(cols[0])

    result = {}
    for name in column_names:
        nullable = (nullable_by_column or {}).get(name)
        if name in pk:
            nullable = False
        result[name] = {
            "is_nullable": nullable,
            "is_unique": name in unique_cols,
            "is_indexed": name in indexed_cols or name in unique_cols,
        }
    return result


def normalize_foreign_keys(foreign_keys: list[dict] | None) -> list[dict] | None:
    """The inspector's foreign keys as {name, columns, referred_table,
    referred_columns} (schema-qualified target names reduced to the table name;
    a target outside the reflected schema is kept and later left unresolved).
    None stays None — 'not reported' — so an old or failed read never erases
    relationships already known."""
    if foreign_keys is None:
        return None
    out = []
    for fk in foreign_keys:
        columns = list(fk.get("constrained_columns") or [])
        referred_columns = list(fk.get("referred_columns") or [])
        table = fk.get("referred_table")
        if not columns or not referred_columns or not table or len(columns) != len(referred_columns):
            continue
        out.append({"name": fk.get("name"), "columns": columns, "referred_table": table, "referred_columns": referred_columns})
    return out
