"""
Pure-Python re-implementation of gateway/gateway/rule_engine.py's rule
primitives, operating on plain list[dict] records instead of pandas
DataFrames. Exists so direct-connection tests (see direct_execution_service)
can be evaluated inside the backend itself without adding a pandas
dependency there — the backend's dependency footprint has deliberately
stayed lean, with pandas kept a Gateway-only need until now. Semantics are
mirrored exactly (same rule_types, same operators, same exception shape) so
a result doesn't differ depending on whether a test happened to run via a
Gateway or directly against a cloud/direct connection.

Records reach this module already JSON-safe (see direct_execution_service's
_json_safe) — a date/datetime field is always an ISO-8601 string by the
time it gets here, never a native datetime object. RelativeDate values
(see app.schemas.test_rule.RelativeDate) are resolved and compared as
timezone-aware datetimes, parsed from those strings.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

_OPERATORS = {
    "eq": lambda v, target: v == target,
    "ne": lambda v, target: v != target,
    "gt": lambda v, target: v is not None and target is not None and v > target,
    "gte": lambda v, target: v is not None and target is not None and v >= target,
    "lt": lambda v, target: v is not None and target is not None and v < target,
    "lte": lambda v, target: v is not None and target is not None and v <= target,
    "is_null": lambda v, _t: v is None,
    "is_not_null": lambda v, _t: v is not None,
    "matches": lambda v, pattern: v is not None and pattern is not None and re.search(str(pattern), str(v)) is not None,
    "in": lambda v, values: v in (values or []),
    "not_in": lambda v, values: v not in (values or []),
}


@dataclass
class RuleResult:
    records_analyzed: int
    exceptions: list[dict[str, Any]]  # each: {"record_identifier": str, "exception_data": {...}}


def _is_relative_date(value: Any) -> bool:
    return isinstance(value, dict) and value.get("kind") == "relative_date"


def _resolve_relative_date(spec: dict) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=spec["relative_days"])


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _matches(record: dict, field: str, operator: str, value: Any) -> bool:
    field_value = record.get(field)
    if _is_relative_date(value):
        return _OPERATORS[operator](_parse_datetime(field_value), _resolve_relative_date(value))
    return _OPERATORS[operator](field_value, value)


def _dynamic_relative_date_matches(date_record: dict, offset_record: dict, spec: dict) -> bool:
    """Shared by cross_match_condition (2-object: date_field always read
    from date_record/primary, offset_field always from offset_record/
    secondary) and three_way_match (3-object: caller passes whichever two
    role-records the spec's date_object/offset_object select). A missing
    date or a missing/non-numeric offset never matches — same is_null-style
    safety as a bad RelativeDate field, not a crash."""
    date_field = spec.get("date_field", spec.get("primary_field"))
    offset_field = spec.get("offset_field", spec.get("secondary_field"))
    date_val = _parse_datetime(date_record.get(date_field))
    offset_val = offset_record.get(offset_field)
    if date_val is None or offset_val is None:
        return False
    try:
        offset_days = float(offset_val)
    except (TypeError, ValueError):
        return False
    direction = spec.get("direction", -1)
    threshold = datetime.now(timezone.utc) + timedelta(days=direction * offset_days)
    return _OPERATORS[spec["operator"]](date_val, threshold)


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
        condition = rule.get("condition")
        distinct_field = rule.get("distinct_field")
        candidates = rows
        if condition is not None:
            candidates = [r for r in candidates if _matches(r, condition["field"], condition["operator"], condition.get("value"))]
        if distinct_field:
            distinct_values: dict[tuple, set] = {}
            for r in candidates:
                key = tuple(r.get(c) for c in group_cols)
                value = r.get(distinct_field)
                if value is not None:
                    distinct_values.setdefault(key, set()).add(value)
            hits = [r for r in candidates if len(distinct_values.get(tuple(r.get(c) for c in group_cols), set())) > 1]
        else:
            counts: dict[tuple, int] = {}
            for r in candidates:
                key = tuple(r.get(c) for c in group_cols)
                counts[key] = counts.get(key, 0) + 1
            hits = [r for r in candidates if counts[tuple(r.get(c) for c in group_cols)] > 1]
        return RuleResult(
            len(rows), [{"record_identifier": _record_identifier(r, group_cols), "exception_data": r} for r in hits]
        )

    if rule_type == "missing_match":
        primary = records[rule["primary_object"]]
        secondary = records[rule["secondary_object"]]
        join_field = rule["join_field"]
        secondary_join_field = rule.get("secondary_join_field") or join_field
        primary_condition = rule.get("primary_condition")
        secondary_condition = rule.get("secondary_condition")

        candidates = primary
        if primary_condition is not None:
            candidates = [
                r for r in candidates if _matches(r, primary_condition["field"], primary_condition["operator"], primary_condition.get("value"))
            ]

        secondary_candidates = secondary
        if secondary_condition is not None:
            secondary_candidates = [
                r
                for r in secondary_candidates
                if _matches(r, secondary_condition["field"], secondary_condition["operator"], secondary_condition.get("value"))
            ]

        matched_keys = {r[secondary_join_field] for r in secondary_candidates if r.get(secondary_join_field) is not None}
        hits = [r for r in candidates if r.get(join_field) not in matched_keys]
        return RuleResult(
            len(primary), [{"record_identifier": _record_identifier(r, [join_field]), "exception_data": r} for r in hits]
        )

    if rule_type == "cross_match_condition":
        primary = records[rule["primary_object"]]
        secondary = records[rule["secondary_object"]]
        join_field = rule["join_field"]
        secondary_join_field = rule.get("secondary_join_field") or join_field
        cp, cs = rule["condition_primary"], rule["condition_secondary"]
        field_comparison = rule.get("field_comparison")
        dynamic_cmp = rule.get("dynamic_relative_date_comparison")

        primary_hits = [r for r in primary if _matches(r, cp["field"], cp["operator"], cp.get("value"))]
        secondary_hits = [r for r in secondary if _matches(r, cs["field"], cs["operator"], cs.get("value"))]
        secondary_by_key: dict[Any, list[dict]] = {}
        for r in secondary_hits:
            secondary_by_key.setdefault(r.get(secondary_join_field), []).append(r)

        overlap_keys = (set().union(*[set(r) for r in primary_hits[:1]]) & set().union(*[set(r) for r in secondary_hits[:1]])) - {
            join_field,
            secondary_join_field,
        }
        exceptions = []
        for pr in primary_hits:
            for sr in secondary_by_key.get(pr.get(join_field), []):
                if field_comparison is not None:
                    op = field_comparison["operator"]
                    if not _OPERATORS[op](pr.get(field_comparison["primary_field"]), sr.get(field_comparison["secondary_field"])):
                        continue
                if dynamic_cmp is not None and not _dynamic_relative_date_matches(pr, sr, dynamic_cmp):
                    continue
                merged = {(f"{k}_primary" if k in overlap_keys else k): v for k, v in pr.items()}
                merged.update({(f"{k}_secondary" if k in overlap_keys else k): v for k, v in sr.items()})
                exceptions.append({"record_identifier": _record_identifier(pr, [join_field]), "exception_data": merged})
        return RuleResult(len(primary), exceptions)

    if rule_type == "three_way_match":
        primary = records[rule["primary_object"]]
        secondary = records[rule["secondary_object"]]
        tertiary = records[rule["tertiary_object"]]
        jf_ps = rule["join_field_primary_secondary"]
        sec_field_1 = rule.get("secondary_join_field_1") or jf_ps
        jf_st = rule["join_field_secondary_tertiary"]
        tert_field = rule.get("tertiary_join_field") or jf_st
        cp, cs, ct = rule.get("condition_primary"), rule.get("condition_secondary"), rule.get("condition_tertiary")
        field_comparison = rule.get("field_comparison")
        dynamic_cmp = rule.get("dynamic_relative_date_comparison")

        primary_hits = [r for r in primary if cp is None or _matches(r, cp["field"], cp["operator"], cp.get("value"))]
        secondary_hits = [r for r in secondary if cs is None or _matches(r, cs["field"], cs["operator"], cs.get("value"))]
        tertiary_hits = [r for r in tertiary if ct is None or _matches(r, ct["field"], ct["operator"], ct.get("value"))]

        secondary_by_key: dict[Any, list[dict]] = {}
        for r in secondary_hits:
            secondary_by_key.setdefault(r.get(sec_field_1), []).append(r)
        tertiary_by_key: dict[Any, list[dict]] = {}
        for r in tertiary_hits:
            tertiary_by_key.setdefault(r.get(tert_field), []).append(r)

        exceptions = []
        for pr in primary_hits:
            for sr in secondary_by_key.get(pr.get(jf_ps), []):
                for tr in tertiary_by_key.get(sr.get(jf_st), []):
                    rows_by_role = {"primary": pr, "secondary": sr, "tertiary": tr}
                    if field_comparison is not None:
                        left = rows_by_role[field_comparison["left_object"]].get(field_comparison["left_field"])
                        right = rows_by_role[field_comparison["right_object"]].get(field_comparison["right_field"])
                        if not _OPERATORS[field_comparison["operator"]](left, right):
                            continue
                    if dynamic_cmp is not None:
                        date_record = rows_by_role[dynamic_cmp["date_object"]]
                        offset_record = rows_by_role[dynamic_cmp["offset_object"]]
                        if not _dynamic_relative_date_matches(date_record, offset_record, dynamic_cmp):
                            continue
                    # Always suffixed by role (unlike the two-object rules'
                    # overlap-only suffixing) — simpler and unambiguous
                    # with three tables in play; exception_data is
                    # informational evidence, not consumed programmatically.
                    merged: dict[str, Any] = {}
                    merged.update({f"{k}_primary": v for k, v in pr.items()})
                    merged.update({f"{k}_secondary": v for k, v in sr.items()})
                    merged.update({f"{k}_tertiary": v for k, v in tr.items()})
                    exceptions.append({"record_identifier": _record_identifier(pr, [jf_ps]), "exception_data": merged})
        return RuleResult(len(primary), exceptions)

    if rule_type == "four_way_match":
        # One hop past three_way_match — e.g. a transaction (purchase_order/
        # payment/journal_entry) -> its own approval record -> the
        # approver's role -> that role's authorised limit, which needs a
        # 4th object no existing primitive reaches (see PR-002/PR-019/
        # GL-007's own rule templates for the concrete shape). Otherwise
        # identical in structure to three_way_match, one level deeper.
        primary = records[rule["primary_object"]]
        secondary = records[rule["secondary_object"]]
        tertiary = records[rule["tertiary_object"]]
        quaternary = records[rule["quaternary_object"]]
        jf_ps = rule["join_field_primary_secondary"]
        sec_field_1 = rule.get("secondary_join_field_1") or jf_ps
        jf_st = rule["join_field_secondary_tertiary"]
        tert_field = rule.get("tertiary_join_field") or jf_st
        jf_tq = rule["join_field_tertiary_quaternary"]
        quat_field = rule.get("quaternary_join_field") or jf_tq
        cp, cs, ct, cq = (
            rule.get("condition_primary"), rule.get("condition_secondary"),
            rule.get("condition_tertiary"), rule.get("condition_quaternary"),
        )
        field_comparison = rule.get("field_comparison")
        dynamic_cmp = rule.get("dynamic_relative_date_comparison")

        primary_hits = [r for r in primary if cp is None or _matches(r, cp["field"], cp["operator"], cp.get("value"))]
        secondary_hits = [r for r in secondary if cs is None or _matches(r, cs["field"], cs["operator"], cs.get("value"))]
        tertiary_hits = [r for r in tertiary if ct is None or _matches(r, ct["field"], ct["operator"], ct.get("value"))]
        quaternary_hits = [r for r in quaternary if cq is None or _matches(r, cq["field"], cq["operator"], cq.get("value"))]

        secondary_by_key: dict[Any, list[dict]] = {}
        for r in secondary_hits:
            secondary_by_key.setdefault(r.get(sec_field_1), []).append(r)
        tertiary_by_key: dict[Any, list[dict]] = {}
        for r in tertiary_hits:
            tertiary_by_key.setdefault(r.get(tert_field), []).append(r)
        quaternary_by_key: dict[Any, list[dict]] = {}
        for r in quaternary_hits:
            quaternary_by_key.setdefault(r.get(quat_field), []).append(r)

        exceptions = []
        for pr in primary_hits:
            for sr in secondary_by_key.get(pr.get(jf_ps), []):
                for tr in tertiary_by_key.get(sr.get(jf_st), []):
                    for qr in quaternary_by_key.get(tr.get(jf_tq), []):
                        rows_by_role = {"primary": pr, "secondary": sr, "tertiary": tr, "quaternary": qr}
                        if field_comparison is not None:
                            left = rows_by_role[field_comparison["left_object"]].get(field_comparison["left_field"])
                            right = rows_by_role[field_comparison["right_object"]].get(field_comparison["right_field"])
                            if not _OPERATORS[field_comparison["operator"]](left, right):
                                continue
                        if dynamic_cmp is not None:
                            date_record = rows_by_role[dynamic_cmp["date_object"]]
                            offset_record = rows_by_role[dynamic_cmp["offset_object"]]
                            if not _dynamic_relative_date_matches(date_record, offset_record, dynamic_cmp):
                                continue
                        merged: dict[str, Any] = {}
                        merged.update({f"{k}_primary": v for k, v in pr.items()})
                        merged.update({f"{k}_secondary": v for k, v in sr.items()})
                        merged.update({f"{k}_tertiary": v for k, v in tr.items()})
                        merged.update({f"{k}_quaternary": v for k, v in qr.items()})
                        exceptions.append({"record_identifier": _record_identifier(pr, [jf_ps]), "exception_data": merged})
        return RuleResult(len(primary), exceptions)

    raise ValueError(f"Unsupported rule_type: {rule_type}")
