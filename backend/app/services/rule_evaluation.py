"""
Pure-Python re-implementation of gateway/gateway/rule_engine.py's four rule
primitives, operating on plain list[dict] records instead of pandas
DataFrames. Exists so direct-connection tests (see direct_execution_service)
can be evaluated inside the backend itself without adding a pandas
dependency there — the backend's dependency footprint has deliberately
stayed lean, with pandas kept a Gateway-only need until now. Semantics are
mirrored exactly (same four rule_types, same operators, same exception
shape) so a result doesn't differ depending on whether a test happened to
run via a Gateway or directly against a cloud/direct connection.
"""
from dataclasses import dataclass
from typing import Any

_OPERATORS = {
    "eq": lambda v, target: v == target,
    "ne": lambda v, target: v != target,
    "gt": lambda v, target: v is not None and v > target,
    "gte": lambda v, target: v is not None and v >= target,
    "lt": lambda v, target: v is not None and v < target,
    "lte": lambda v, target: v is not None and v <= target,
    "is_null": lambda v, _t: v is None,
    "is_not_null": lambda v, _t: v is not None,
}


@dataclass
class RuleResult:
    records_analyzed: int
    exceptions: list[dict[str, Any]]  # each: {"record_identifier": str, "exception_data": {...}}


def _matches(record: dict, field: str, operator: str, value: Any) -> bool:
    return _OPERATORS[operator](record.get(field), value)


def _record_identifier(record: dict, prefer: list[str]) -> str:
    for col in prefer:
        if record.get(col) is not None:
            return str(record[col])
    return str(next(iter(record.values()), ""))


def evaluate(rule: dict, records: dict[str, list[dict]]) -> RuleResult:
    rule_type = rule["rule_type"]

    if rule_type == "threshold":
        rows = records[rule["object"]]
        hits = [r for r in rows if _matches(r, rule["field"], rule["operator"], rule["value"])]
        return RuleResult(
            len(rows), [{"record_identifier": _record_identifier(r, [rule["field"]]), "exception_data": r} for r in hits]
        )

    if rule_type == "duplicate":
        rows = records[rule["object"]]
        group_cols = rule["group_by"]
        counts: dict[tuple, int] = {}
        for r in rows:
            key = tuple(r.get(c) for c in group_cols)
            counts[key] = counts.get(key, 0) + 1
        hits = [r for r in rows if counts[tuple(r.get(c) for c in group_cols)] > 1]
        return RuleResult(
            len(rows), [{"record_identifier": _record_identifier(r, group_cols), "exception_data": r} for r in hits]
        )

    if rule_type == "missing_match":
        primary = records[rule["primary_object"]]
        secondary = records[rule["secondary_object"]]
        join_field = rule["join_field"]
        matched_keys = {r[join_field] for r in secondary if r.get(join_field) is not None}
        hits = [r for r in primary if r.get(join_field) not in matched_keys]
        return RuleResult(
            len(primary), [{"record_identifier": _record_identifier(r, [join_field]), "exception_data": r} for r in hits]
        )

    if rule_type == "cross_match_condition":
        primary = records[rule["primary_object"]]
        secondary = records[rule["secondary_object"]]
        join_field = rule["join_field"]
        cp, cs = rule["condition_primary"], rule["condition_secondary"]

        primary_hits = [r for r in primary if _matches(r, cp["field"], cp["operator"], cp.get("value"))]
        secondary_hits = [r for r in secondary if _matches(r, cs["field"], cs["operator"], cs.get("value"))]
        secondary_by_key: dict[Any, list[dict]] = {}
        for r in secondary_hits:
            secondary_by_key.setdefault(r.get(join_field), []).append(r)

        overlap_keys = (set().union(*[set(r) for r in primary_hits[:1]]) & set().union(*[set(r) for r in secondary_hits[:1]])) - {
            join_field
        }
        exceptions = []
        for pr in primary_hits:
            for sr in secondary_by_key.get(pr.get(join_field), []):
                merged = {(f"{k}_primary" if k in overlap_keys else k): v for k, v in pr.items()}
                merged.update({(f"{k}_secondary" if k in overlap_keys else k): v for k, v in sr.items()})
                exceptions.append({"record_identifier": _record_identifier(pr, [join_field]), "exception_data": merged})
        return RuleResult(len(primary), exceptions)

    raise ValueError(f"Unsupported rule_type: {rule_type}")
