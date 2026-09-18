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
    "matches": lambda s, v: s.astype(str).str.contains(str(v), regex=True, na=False) if v is not None else pd.Series(False, index=s.index),
    "in": lambda s, v: s.isin(v or []),
    "not_in": lambda s, v: ~s.isin(v or []),
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


def _dynamic_relative_date_mask(date_series: pd.Series, offset_series: pd.Series, operator: str, direction: int) -> pd.Series:
    """Shared by cross_match_condition and three_way_match: builds the
    row-wise now()+/-offset threshold from a per-row day-count column and
    compares date_series to it. coerce=NaT/NaN for anything unparseable —
    same is_null-style safety _apply_condition already uses for RelativeDate
    — then those rows simply never satisfy any comparison operator, rather
    than raising."""
    dates = pd.to_datetime(date_series, errors="coerce", utc=True)
    offsets = pd.to_numeric(offset_series, errors="coerce")
    threshold = pd.Timestamp(datetime.now(timezone.utc)) + pd.to_timedelta(direction * offsets, unit="D")
    return _OPERATORS[operator](dates, threshold).fillna(False)


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
        condition = rule.get("condition")
        distinct_field = rule.get("distinct_field")
        candidates = df if condition is None else df[_apply_condition(df, condition["field"], condition["operator"], condition.get("value"))]
        if distinct_field:
            # nunique() ignores NaN by default, matching the "missing values
            # don't count toward distinctness" rule in the pure-Python evaluator.
            distinct_counts = candidates.groupby(group_cols)[distinct_field].transform("nunique")
            hits = candidates[distinct_counts > 1]
        else:
            counts = candidates.groupby(group_cols)[group_cols[0]].transform("size")
            hits = candidates[counts > 1]
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
        secondary_condition = rule.get("secondary_condition")

        candidates = primary
        if primary_condition is not None:
            candidates = primary[_apply_condition(primary, primary_condition["field"], primary_condition["operator"], primary_condition.get("value"))]

        secondary_candidates = secondary
        if secondary_condition is not None:
            secondary_candidates = secondary[
                _apply_condition(secondary, secondary_condition["field"], secondary_condition["operator"], secondary_condition.get("value"))
            ]

        matched_keys = set(secondary_candidates[secondary_join_field].dropna())
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
        dynamic_cmp = rule.get("dynamic_relative_date_comparison")

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
        if dynamic_cmp is not None:
            primary_hits = primary_hits.rename(columns={dynamic_cmp["primary_field"]: "__dyn_date__"})
            secondary_hits = secondary_hits.rename(columns={dynamic_cmp["secondary_field"]: "__dyn_offset__"})

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

        if dynamic_cmp is not None:
            mask = _dynamic_relative_date_mask(
                merged["__dyn_date__"], merged["__dyn_offset__"], dynamic_cmp["operator"], dynamic_cmp.get("direction", -1)
            )
            merged = merged[mask]
            merged = merged.rename(columns={"__dyn_date__": dynamic_cmp["primary_field"], "__dyn_offset__": dynamic_cmp["secondary_field"]})

        return RuleResult(
            records_analyzed=len(primary),
            exceptions=[
                {"record_identifier": _record_identifier(row, [join_field]), "exception_data": _json_safe_row(row)} for _, row in merged.iterrows()
            ],
        )

    if rule_type == "three_way_match":
        primary = dataframes[rule["primary_object"]]
        secondary = dataframes[rule["secondary_object"]]
        tertiary = dataframes[rule["tertiary_object"]]
        jf_ps = rule["join_field_primary_secondary"]
        sec_field_1 = rule.get("secondary_join_field_1") or jf_ps
        jf_st = rule["join_field_secondary_tertiary"]
        tert_field = rule.get("tertiary_join_field") or jf_st
        cp, cs, ct = rule.get("condition_primary"), rule.get("condition_secondary"), rule.get("condition_tertiary")
        field_comparison = rule.get("field_comparison")
        dynamic_cmp = rule.get("dynamic_relative_date_comparison")

        primary_hits = primary if cp is None else primary[_apply_condition(primary, cp["field"], cp["operator"], cp.get("value"))]
        secondary_hits = secondary if cs is None else secondary[_apply_condition(secondary, cs["field"], cs["operator"], cs.get("value"))]
        tertiary_hits = tertiary if ct is None else tertiary[_apply_condition(tertiary, ct["field"], ct["operator"], ct.get("value"))]

        # Every column suffixed by its role BEFORE either merge — with
        # three tables, trying to track pandas' automatic overlap-only
        # suffixing (as cross_match_condition does for two) gets genuinely
        # ambiguous; doing it upfront makes every column name unique and
        # unambiguous by construction, including the join keys themselves
        # (referenced below as "<field>_primary" etc.).
        primary_hits = primary_hits.add_suffix("_primary")
        secondary_hits = secondary_hits.add_suffix("_secondary")
        tertiary_hits = tertiary_hits.add_suffix("_tertiary")

        merged = primary_hits.merge(secondary_hits, left_on=f"{jf_ps}_primary", right_on=f"{sec_field_1}_secondary")
        merged = merged.merge(tertiary_hits, left_on=f"{jf_st}_secondary", right_on=f"{tert_field}_tertiary")

        if field_comparison is not None:
            left_col = f"{field_comparison['left_field']}_{field_comparison['left_object']}"
            right_col = f"{field_comparison['right_field']}_{field_comparison['right_object']}"
            merged = merged[_OPERATORS[field_comparison["operator"]](merged[left_col], merged[right_col])]

        if dynamic_cmp is not None:
            # Every column was suffixed by role before either merge (see
            # above), so — unlike cross_match_condition's two-object rename
            # trick — the role-suffixed column names are already unique and
            # can be addressed directly with no rename needed.
            date_col = f"{dynamic_cmp['date_field']}_{dynamic_cmp['date_object']}"
            offset_col = f"{dynamic_cmp['offset_field']}_{dynamic_cmp['offset_object']}"
            mask = _dynamic_relative_date_mask(merged[date_col], merged[offset_col], dynamic_cmp["operator"], dynamic_cmp.get("direction", -1))
            merged = merged[mask]

        return RuleResult(
            records_analyzed=len(primary),
            exceptions=[
                {"record_identifier": _record_identifier(row, [f"{jf_ps}_primary"]), "exception_data": _json_safe_row(row)}
                for _, row in merged.iterrows()
            ],
        )

    if rule_type == "four_way_match":
        # One hop past three_way_match — see app.services.rule_evaluation's
        # mirror of this block for the rationale (a transaction -> its own
        # approval record -> the approver's role -> that role's authorised
        # limit needs a 4th object no existing primitive reaches).
        primary = dataframes[rule["primary_object"]]
        secondary = dataframes[rule["secondary_object"]]
        tertiary = dataframes[rule["tertiary_object"]]
        quaternary = dataframes[rule["quaternary_object"]]
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

        primary_hits = primary if cp is None else primary[_apply_condition(primary, cp["field"], cp["operator"], cp.get("value"))]
        secondary_hits = secondary if cs is None else secondary[_apply_condition(secondary, cs["field"], cs["operator"], cs.get("value"))]
        tertiary_hits = tertiary if ct is None else tertiary[_apply_condition(tertiary, ct["field"], ct["operator"], ct.get("value"))]
        quaternary_hits = quaternary if cq is None else quaternary[_apply_condition(quaternary, cq["field"], cq["operator"], cq.get("value"))]

        # Suffixed by role before any merge, same reasoning as
        # three_way_match — unambiguous column names by construction,
        # including the join keys themselves, across all four tables.
        primary_hits = primary_hits.add_suffix("_primary")
        secondary_hits = secondary_hits.add_suffix("_secondary")
        tertiary_hits = tertiary_hits.add_suffix("_tertiary")
        quaternary_hits = quaternary_hits.add_suffix("_quaternary")

        merged = primary_hits.merge(secondary_hits, left_on=f"{jf_ps}_primary", right_on=f"{sec_field_1}_secondary")
        merged = merged.merge(tertiary_hits, left_on=f"{jf_st}_secondary", right_on=f"{tert_field}_tertiary")
        merged = merged.merge(quaternary_hits, left_on=f"{jf_tq}_tertiary", right_on=f"{quat_field}_quaternary")

        if field_comparison is not None:
            left_col = f"{field_comparison['left_field']}_{field_comparison['left_object']}"
            right_col = f"{field_comparison['right_field']}_{field_comparison['right_object']}"
            merged = merged[_OPERATORS[field_comparison["operator"]](merged[left_col], merged[right_col])]

        if dynamic_cmp is not None:
            date_col = f"{dynamic_cmp['date_field']}_{dynamic_cmp['date_object']}"
            offset_col = f"{dynamic_cmp['offset_field']}_{dynamic_cmp['offset_object']}"
            mask = _dynamic_relative_date_mask(merged[date_col], merged[offset_col], dynamic_cmp["operator"], dynamic_cmp.get("direction", -1))
            merged = merged[mask]

        return RuleResult(
            records_analyzed=len(primary),
            exceptions=[
                {"record_identifier": _record_identifier(row, [f"{jf_ps}_primary"]), "exception_data": _json_safe_row(row)}
                for _, row in merged.iterrows()
            ],
        )

    raise ValueError(f"Unsupported rule_type: {rule_type}")
