"""
Finding relationships a client's schema does not declare.

Many databases have no foreign keys at all (MongoDB always; Snowflake and SAP
HANA usually; plenty of legacy SQL Server/Oracle systems by design). Their
relationships still exist — `api_access.user_id` holds values that are user
ids — they are just not written down. This module decides WHICH column pairs
are worth measuring and how to score what the measurement found; the queries
themselves live in data_source_service (they need a connection).

Deliberately cautious, because a wrong edge is worse than a missing one:
  * a PARENT must look like a key (declared primary key/unique, or a sample in
    which every value was distinct) — a status column can't be a parent;
  * a pair is only worth measuring if the NAMES suggest it (identical, or the
    child is `<parent table>_id`, or one name contains the other) — pairing every
    id-shaped column with every other would manufacture edges;
  * tiny cardinality proves nothing (a child with 1 distinct value is
    contained in almost any parent), so a minimum is required;
  * the measurement is exact containment on the client's data, not a sample.

Only aggregate numbers leave the client's database: how many distinct child
values there are, and how many of them exist on the parent side.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.value_profile import column_kind_from_type

# Below this many distinct child values containment says nothing useful.
MIN_CHILD_DISTINCT = 2
# An inferred edge below this containment is noise, not a relationship.
MIN_STORED_CONTAINMENT = 0.5
# A parent side that isn't declared unique must at least have looked unique in a sample.
MIN_PARENT_DISTINCT_RATIO = 0.99
MIN_NAME_AFFINITY = 0.5
MAX_CANDIDATES_PER_CHILD = 5
# A table with fewer rows than this can't vouch that a column is a key: every
# value in a 2-row table is "distinct".
MIN_PARENT_SAMPLE = 3
# The last word of a column that names a row (order_id, po_number, supplier_code...).
_KEY_TOKENS = {"id", "no", "number", "code", "key", "ref", "uuid", "guid"}


@dataclass(frozen=True)
class ColumnRef:
    field_id: str
    entity_id: str
    entity_name: str
    name: str
    data_type: str | None = None
    is_primary_key: bool = False
    is_unique: bool = False
    distinct_ratio: float | None = None  # from a profile, when one exists
    sample_size: int = 0
    null_ratio: float | None = None


@dataclass(frozen=True)
class Candidate:
    child: ColumnRef
    parent: ColumnRef
    name_affinity: float
    parent_unique: bool  # declared unique/PK (True) vs only looked unique in a sample (False)
    # True when a control's rule needs these two columns joined: the control, not
    # their names, says they should relate, so they are measured whatever they are
    # called and whether or not the parent side is a key.
    requirement_driven: bool = False


@dataclass(frozen=True)
class Measurement:
    child_distinct: int
    matched_distinct: int  # child distinct values that also exist on the parent side
    parent_distinct: int
    capped: bool = False  # the count hit a safety cap; the numbers are lower bounds
    parent_rows: int | None = None  # non-null rows on the parent side, to check it really is a key

    @property
    def containment(self) -> float:
        return self.matched_distinct / self.child_distinct if self.child_distinct else 0.0

    @property
    def parent_is_key(self) -> bool:
        """The parent side really is a key on the client's data: enough rows to
        mean something, every one of them distinct."""
        return (
            self.parent_rows is not None
            and self.parent_rows >= MIN_PARENT_SAMPLE
            and self.parent_distinct >= MIN_PARENT_DISTINCT_RATIO * self.parent_rows
        )


_SYNTHETIC = {"_id"}


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]


def singular(word: str) -> str:
    w = word.lower()
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("ses") or w.endswith("xes"):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def name_affinity(child: ColumnRef, parent: ColumnRef) -> float:
    """0..1: how strongly the two NAMES suggest child references parent."""
    c = "_".join(_tokens(child.name))
    p = "_".join(_tokens(parent.name))
    if not c or not p:
        return 0.0
    if c == p:
        return 1.0
    # child is "<parent table singular>_<parent column>", e.g. user_id -> users.id
    # (or the table name repeated, users.user_id, which the identical case covers)
    table = "_".join(singular(t) for t in _tokens(parent.entity_name))
    if p in {"id", "_id"} and c in {f"{table}_id", f"{table}id", f"{table}_key", f"{table}_no", f"{table}_number"}:
        return 0.9
    ct, pt = set(_tokens(child.name)), set(_tokens(parent.name))
    # one name contained in the other: role -> role_name, dept -> dept_code
    if ct and pt and (ct < pt or pt < ct):
        return 0.6
    return 0.0


def looks_like_key_name(col: ColumnRef) -> bool:
    """Does the NAME say this column identifies rows? order_id, po_number,
    role_name in `roles`, username. A status, a flag, a timestamp or an amount
    never does, however few distinct values a small sample happened to show."""
    tokens = _tokens(col.name)
    if not tokens:
        return False
    if tokens[-1] in _KEY_TOKENS or col.name.lower() == "username":
        return True
    # "<table>_name" is a table's natural key: roles.role_name, departments.department_name
    entity_stems = {singular(t) for t in _tokens(col.entity_name)}
    return tokens[-1] == "name" and len(tokens) > 1 and bool(entity_stems & {singular(t) for t in tokens[:-1]})


def is_key_typed(col: ColumnRef) -> bool:
    """Keys are text, identifiers or whole numbers. Dates, booleans and
    fractional numbers (amounts, limits, rates) are not."""
    kind = column_kind_from_type(col.data_type)
    if kind is None:
        return True  # a type we can't classify: let the measurement decide
    if kind in {"text", "identifier"}:
        return True
    if kind == "numeric":
        t = (col.data_type or "").lower()
        return any(w in t for w in ("int", "serial", "long")) and not any(w in t for w in ("double", "float", "decimal", "money", "real"))
    return False


def _entity_relation(child: ColumnRef, parent: ColumnRef) -> int:
    """1 when the parent TABLE is what the child column is named after
    (supplier_id -> suppliers, user_id -> system_users)."""
    child_tokens = set(_tokens(child.name))
    stems = [singular(t) for t in _tokens(parent.entity_name)]
    return 1 if stems and stems[-1] in child_tokens else 0


def _parent_kind_ok(child: ColumnRef, parent: ColumnRef) -> bool:
    ck, pk = column_kind_from_type(child.data_type), column_kind_from_type(parent.data_type)
    if ck is None or pk is None:
        # A type we can't classify (Mongo "mixed", arrays): don't rule it out, the
        # measurement itself decides.
        return True
    compatible = {"identifier", "text"}
    return ck == pk or (ck in compatible and pk in compatible)


def _parent_looks_like_key(parent: ColumnRef) -> tuple[bool, bool]:
    """(eligible as a parent, declared unique)."""
    if parent.name in _SYNTHETIC:
        return False, False
    if not is_key_typed(parent):
        return False, False
    if parent.is_primary_key or parent.is_unique:
        return True, True
    if not looks_like_key_name(parent):
        return False, False
    if parent.distinct_ratio is None:
        # No profile yet: the measurement itself confirms the parent is a key
        # (Measurement.parent_is_key), so absence of a profile isn't a veto.
        return True, False
    if (
        parent.distinct_ratio >= MIN_PARENT_DISTINCT_RATIO
        and parent.sample_size >= MIN_PARENT_SAMPLE
        and (parent.null_ratio or 0.0) < 0.5
    ):
        return True, False
    return False, False


def generate_candidates(columns: list[ColumnRef], *, max_per_child: int = MAX_CANDIDATES_PER_CHILD) -> list[Candidate]:
    """Column pairs worth measuring: a key-like parent in one table, a
    name-affine, type-compatible child in another."""
    parents = []
    for col in columns:
        eligible, declared = _parent_looks_like_key(col)
        if eligible:
            parents.append((col, declared))

    by_child: dict[str, list[Candidate]] = {}
    for child in columns:
        if child.name in _SYNTHETIC or not is_key_typed(child):
            continue
        for parent, declared in parents:
            if parent.entity_id == child.entity_id or parent.field_id == child.field_id:
                continue
            if not _parent_kind_ok(child, parent):
                continue
            affinity = name_affinity(child, parent)
            if affinity < MIN_NAME_AFFINITY:
                continue
            by_child.setdefault(child.field_id, []).append(Candidate(child, parent, affinity, declared))

    out: list[Candidate] = []
    for candidates in by_child.values():
        candidates.sort(
            key=lambda c: (c.name_affinity, c.parent_unique, _entity_relation(c.child, c.parent), c.parent.sample_size),
            reverse=True,
        )
        out.extend(candidates[:max_per_child])
    return out


def score_inferred(candidate: Candidate, m: Measurement) -> tuple[float, str]:
    """(confidence 0-100, cardinality) for a measured candidate."""
    cardinality = "one_to_one" if candidate.child.is_unique else "many_to_one"
    score = (
        45.0 * m.containment
        + (20.0 if (candidate.parent_unique or m.parent_is_key) else 10.0)
        + 25.0 * candidate.name_affinity
        + (10.0 if m.child_distinct >= 5 else 0.0)
    )
    return round(min(score, 100.0), 2), cardinality


def worth_storing(m: Measurement, affinity: float = 0.0) -> bool:
    """Keep an edge when the data supports it — or when the NAMES say these two
    columns should relate (identical, or `<table>_id`) and the data says they
    don't: that low-containment edge is the evidence a mapping is wrong
    (api_access.user_id whose values aren't user ids). Aggregate numbers only."""
    if m.child_distinct < MIN_CHILD_DISTINCT:
        return False
    return m.containment >= MIN_STORED_CONTAINMENT or affinity >= 0.9
