"""
Evaluates a rule definition (pulled from the platform, in terms of
canonical field names) against DataFrames fetched from the client's own
database. Mirrors the four rule primitives in the platform's
app.schemas.test_rule — see that file for the design rationale. This is
its own small implementation rather than a shared import because the
Gateway and the platform backend are genuinely separate applications
(the Gateway ships to a client's machine); duplicating ~100 lines here is
cheaper than coupling them.
"""
from dataclasses import dataclass
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


def _apply_condition(df: pd.DataFrame, field: str, operator: str, value: Any) -> pd.Series:
    return _OPERATORS[operator](df[field], value)


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
        matched_keys = set(secondary[join_field].dropna())
        hits = primary[~primary[join_field].isin(matched_keys)]
        return RuleResult(
            records_analyzed=len(primary),
            exceptions=[{"record_identifier": _record_identifier(row, [join_field]), "exception_data": _json_safe_row(row)} for _, row in hits.iterrows()],
        )

    if rule_type == "cross_match_condition":
        primary = dataframes[rule["primary_object"]]
        secondary = dataframes[rule["secondary_object"]]
        join_field = rule["join_field"]
        cp, cs = rule["condition_primary"], rule["condition_secondary"]

        primary_hits = primary[_apply_condition(primary, cp["field"], cp["operator"], cp.get("value"))]
        secondary_hits = secondary[_apply_condition(secondary, cs["field"], cs["operator"], cs.get("value"))]

        merged = primary_hits.merge(secondary_hits, on=join_field, suffixes=("_primary", "_secondary"))
        return RuleResult(
            records_analyzed=len(primary),
            exceptions=[
                {"record_identifier": _record_identifier(row, [join_field]), "exception_data": _json_safe_row(row)} for _, row in merged.iterrows()
            ],
        )

    raise ValueError(f"Unsupported rule_type: {rule_type}")
