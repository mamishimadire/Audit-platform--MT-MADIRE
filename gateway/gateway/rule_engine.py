"""
Evaluates a rule definition (pulled from the platform, in terms of
canonical field names) against DataFrames fetched from the client's own
database. Mirrors the rule primitives in the platform's app.schemas.test_rule
— see that file for the design rationale. This is its own small
implementation rather than a shared import because the Gateway and the
platform backend are genuinely separate applications (the Gateway ships to
a client's machine); duplicating the logic here is cheaper than coupling
them. app.services.rule_evaluation is the platform backend's own
pandas-free mirror of this file (used for direct-connection tests) — keep
both in sync when either changes.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

_OPERATORS = {
    "eq": lambda s, v: s == v,
    "ne": lambda s, v: s != v,
    "gt": lambda s, v: s > v,
    "gte": lambda s, v: s >= v,
    "lt": lambda s, v: s < v,
    "lte": lambda s, v: s <= v,
    "is_null": lambda s, _v: s.isna(),
    "is_not_null": lambda s, _v: s.notna(),
}


@dataclass
class RuleResult:
    records_analyzed: int
    exceptions: list[dict[str, Any]]  # each: {"record_identifier": str, "exception_data": {...}}


def _is_relative_date(value: Any) -> bool:
    return isinstance(value, dict) and value.get("kind") == "relative_date"


def _resolve_relative_date(spec: dict) -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc) + timedelta(days=spec["relative_days"]))


def _apply_condition(df: pd.DataFrame, field: str, operator: str, value: Any) -> pd.Series:
    series = df[field]
    if _is_relative_date(value):
        # coerce=NaT for anything unparseable rather than raising — a bad/
        # missing date on one row must not fail the whole rule, is_null-
        # style safety (NaT compares False against every operator here).
        series = pd.to_datetime(series, errors="coerce", utc=True)
        return _OPERATORS[operator](series, _resolve_relative_date(value))
    return _OPERATORS[operator](series, value)


def _record_identifier(row: pd.Series, prefer: list[str]) -> str:
    for col in prefer:
        if col in row and pd.notna(row[col]):
            return str(row[col])
    return str(row.iloc[0])


def _json_safe_row(row: pd.Series) -> dict[str, Any]:
    """Converts numpy/pandas scalar types (int64, Timestamp, NaN, ...) to plain JSON-serializable values."""
    result: dict[str, Any] = {}
    for key, value in row.items():
        if pd.isna(value):
            result[key] = None
        elif isinstance(value, pd.Timestamp):
            result[key] = value.isoformat()
        elif hasattr(value, "item"):
            result[key] = value.item()
        else:
            result[key] = value
    return result


def evaluate(rule: dict, dataframes: dict[str, pd.DataFrame]) -> RuleResult:
    rule_type = rule["rule_type"]
    if rule_type == "threshold":
        df = dataframes[rule["object"]]
        mask = _apply_condition(df, rule["field"], rule["operator"], rule["value"])
        hits = df[mask]
        return RuleResult(
            records_analyzed=len(df),
            exceptions=[{"record_identifier": _record_identifier(row, [rule["field"]]), "exception_data": _json_safe_row(row)} for _, row in hits.iterrows()],
        )

    if rule_type == "duplicate":
        df = dataframes[rule["object"]]
        group_cols = rule["group_by"]
        counts = df.groupby(group_cols)[group_cols[0]].transform("size")
        hits = df[counts > 1]
        return RuleResult(
            records_analyzed=len(df),
            exceptions=[{"record_identifier": _record_identifier(row, group_cols), "exception_data": _json_safe_row(row)} for _, row in hits.iterrows()],
        )

    if rule_type == "missing_match":
        primary = dataframes[rule["primary_object"]]
        secondary = dataframes[rule["secondary_object"]]
        join_field = rule["join_field"]
        secondary_join_field = rule.get("secondary_join_field") or join_field
        primary_condition = rule.get("primary_condition")

        candidates = primary
        if primary_condition is not None:
            candidates = primary[_apply_condition(primary, primary_condition["field"], primary_condition["operator"], primary_condition.get("value"))]

        matched_keys = set(secondary[secondary_join_field].dropna())
        hits = candidates[~candidates[join_field].isin(matched_keys)]
        return RuleResult(
            records_analyzed=len(primary),
            exceptions=[{"record_identifier": _record_identifier(row, [join_field]), "exception_data": _json_safe_row(row)} for _, row in hits.iterrows()],
        )

    if rule_type == "cross_match_condition":
        primary = dataframes[rule["primary_object"]]
        secondary = dataframes[rule["secondary_object"]]
        join_field = rule["join_field"]
        secondary_join_field = rule.get("secondary_join_field") or join_field
        cp, cs = rule["condition_primary"], rule["condition_secondary"]
        field_comparison = rule.get("field_comparison")

        primary_hits = primary[_apply_condition(primary, cp["field"], cp["operator"], cp.get("value"))].copy()
        secondary_hits = secondary[_apply_condition(secondary, cs["field"], cs["operator"], cs.get("value"))].copy()

        # Renamed to guaranteed-unique temp columns before merging so the
        # post-join comparison always targets the right column regardless
        # of how pandas' automatic _primary/_secondary suffixing lands
        # (which depends on which other column names happen to collide,
        # including the case where primary_object == secondary_object —
        # the self-join pattern — and every column collides).
        if field_comparison is not None:
            primary_hits = primary_hits.rename(columns={field_comparison["primary_field"]: "__cmp_primary__"})
            secondary_hits = secondary_hits.rename(columns={field_comparison["secondary_field"]: "__cmp_secondary__"})

        if secondary_join_field == join_field:
            merged = primary_hits.merge(secondary_hits, on=join_field, suffixes=("_primary", "_secondary"))
        else:
            merged = primary_hits.merge(secondary_hits, left_on=join_field, right_on=secondary_join_field, suffixes=("_primary", "_secondary"))

        if field_comparison is not None:
            op = field_comparison["operator"]
            merged = merged[_OPERATORS[op](merged["__cmp_primary__"], merged["__cmp_secondary__"])]
            merged = merged.rename(
                columns={"__cmp_primary__": field_comparison["primary_field"], "__cmp_secondary__": field_comparison["secondary_field"]}
            )

        return RuleResult(
            records_analyzed=len(primary),
            exceptions=[
                {"record_identifier": _record_identifier(row, [join_field]), "exception_data": _json_safe_row(row)} for _, row in merged.iterrows()
            ],
        )

    raise ValueError(f"Unsupported rule_type: {rule_type}")
