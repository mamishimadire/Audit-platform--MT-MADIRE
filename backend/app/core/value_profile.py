"""
Value-level understanding of a client column: what it actually holds, and
whether that is the KIND of thing a canonical field needs.

Name similarity (canonical_model) can say `status` looks like `user.status`;
it cannot say the column is full of movie genres, or that `last_login` is a
free-text column, or that a `user_id` is 90% null. This module adds that
second, independent signal from two cheap sources:

  * the column's declared data type (already discovered, works for Gateway
    sources too), and
  * a small profile of sampled values (direct connections; see
    data_source_service.profile_entity_columns).

It only ever judges what it can actually check: every scorer returns None
when there is nothing checkable, and callers use it to DEMOTE a
contradiction, never to boost a match — names stay the primary signal.

Privacy: a profile stores real values only for small, REPETITIVE (enum-like)
columns that are not sensitive, personal or identifiers (see may_store_values). Everything else keeps
statistics only. That policy lives here, next to the code that builds the
profile, so no caller can persist more than it allows.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Literal, Sequence

from app.core.canonical_model import (
    CANONICAL_MODEL,
    LOW_CONTENT_FIT_THRESHOLD,
    infer_object_for_entity,
    score_table_content_fit,
    suggest_canonical_field,
)

Kind = Literal["identifier", "datetime", "boolean", "numeric", "enum", "text"]

# A value-level fit below this is a contradiction (same bar as the table-level
# content fit): the column holds a different kind of thing than the field needs.
LOW_VALUE_FIT_THRESHOLD = LOW_CONTENT_FIT_THRESHOLD
# A contradicted mapping is capped here so it can never auto-accept (the
# default bar is AUTO_ACCEPT_THRESHOLD=90); a human confirms it instead.
VALUE_CONTRADICTION_CONFIDENCE_CAP = 74.0
# Below this many checkable columns a table-level value verdict would rest on
# one coincidence, so it stays silent.
MIN_CHECKABLE_FIELDS_FOR_TABLE_VERDICT = 2

# ---- privacy policy -------------------------------------------------------
MAX_STORED_DISTINCT_VALUES = 20
MAX_STORED_VALUE_LENGTH = 40
MIN_SAMPLE_FOR_ENUM_JUDGEMENT = 20
# Two values from three rows say nothing about what a status column holds.
MIN_SAMPLE_FOR_VOCABULARY_CHECK = 5
# Long tokens match anywhere in the (underscore-normalised) name; short ones
# only as a whole word, so "pin"/"pay"/"tax" don't swallow "shipping",
# "payment_status" or "syntax".
_PII_SUBSTRINGS = (
    "password", "passwd", "hash", "secret", "token", "credential", "apikey", "api_key", "passport",
    "national_id", "id_number", "email", "phone", "mobile", "address", "street", "postcode", "salary",
    "birth", "gender", "ethnic", "religion", "medical", "diagnos", "first_name", "last_name", "full_name",
    "surname", "account_number", "iban", "username", "user_name", "login_name", "loginname", "nickname",
    "display_name", "screen_name", "handle",
)
_PII_WORDS = frozenset(
    "pwd salt ssn social tax mail cell fax zip pay wage bank card cvv pin dob health".split()
)


# The last word of a column that identifies rows (user_id, invoice_no, session_key...). Its
# values ARE the identifiers, however few rows a small table happens to have.
_IDENTIFIER_NAME_WORDS = frozenset({"id", "no", "num", "number", "key", "uuid", "guid", "ref", "reference"})


def is_identifier_named(field_name: str) -> bool:
    words = [w for w in re.sub(r"[^a-z0-9]+", "_", field_name.lower()).split("_") if w]
    return bool(words) and words[-1] in _IDENTIFIER_NAME_WORDS


# Only a column whose NAME says it is a category may have its values kept. Values are used to
# check that a status/state column holds states, and nothing else needs them, so everything
# else (people, free text, amounts, codes, names) stays statistics-only: collect what is needed.
_CATEGORICAL_NAME_WORDS = frozenset(
    {
        "status", "state", "type", "category", "level", "severity", "priority", "stage", "class",
        "classification", "criticality", "sensitivity", "outcome", "result", "action", "frequency", "mode", "kind",
    }
)


def is_categorical_named(field_name: str) -> bool:
    words = [w for w in re.sub(r"[^a-z0-9]+", "_", field_name.lower()).split("_") if w]
    return any(w in _CATEGORICAL_NAME_WORDS for w in words)


def is_pii_named(field_name: str) -> bool:
    lowered = re.sub(r"[^a-z0-9]+", "_", field_name.lower()).strip("_")
    if any(token in lowered for token in _PII_SUBSTRINGS):
        return True
    return any(word in _PII_WORDS for word in lowered.split("_"))


# ---- what a canonical field expects ---------------------------------------
_BOOLEAN_PREFIXES = ("is_", "has_", "can_", "was_")
_BOOLEAN_SUFFIXES = ("_enabled", "_allowed", "_used", "_active", "_required")
_BOOLEAN_NAMES = {
    "approved", "authorized", "blocked", "encrypted", "installed", "logged", "on_file", "on_network",
    "registered", "resolved", "tested", "emergency",
}
_DATETIME_SUFFIXES = ("_at", "_date", "_time", "_timestamp")
_IDENTIFIER_SUFFIXES = ("_id", "_ref", "_number", "_no", "_code")
_NUMERIC_SUFFIXES = (
    "_amount", "_days", "_count", "_total", "_score", "_rate", "_percent", "_pct", "_hours", "_minutes",
    "_seconds", "_pay", "_salary", "_quantity", "_limit", "_revenue",
)
_NUMERIC_NAMES = {"amount", "balance", "quantity", "credit", "debit", "port", "quantity_on_hand", "days_overdue"}
_ENUM_SUFFIXES = ("_status", "_state", "_type", "_level", "_category", "_severity", "_priority", "_stage")
_ENUM_NAMES = {
    "status", "state", "type", "level", "category", "severity", "priority", "sensitivity", "classification",
    "criticality", "outcome", "frequency",
}
_STATE_NAMES_SUFFIXES = ("_status", "_state")
_STATE_NAMES = {"status", "state"}


def _bare(canonical_field: str) -> str:
    return canonical_field.split(".", 1)[1] if "." in canonical_field else canonical_field


def canonical_kind(canonical_field: str) -> Kind | None:
    """The kind of value a canonical field conventionally holds, from its
    NAME. None = nothing reliable to check, and the scorers stay silent."""
    name = _bare(canonical_field).lower()
    if name.startswith(_BOOLEAN_PREFIXES) or name.endswith(_BOOLEAN_SUFFIXES) or name in _BOOLEAN_NAMES:
        return "boolean"
    if name in _ENUM_NAMES or name.endswith(_ENUM_SUFFIXES):
        return "enum"
    is_last_event = name.startswith("last_") and any(
        token in name for token in ("login", "logon", "seen", "access", "activity", "run", "backup", "scan", "update")
    )
    if name.endswith(_DATETIME_SUFFIXES) or is_last_event:
        return "datetime"
    if name.endswith(_NUMERIC_SUFFIXES) or name in _NUMERIC_NAMES or name.startswith("days_"):
        return "numeric"
    if name.endswith(_IDENTIFIER_SUFFIXES):
        return "identifier"
    return None


def is_state_field(canonical_field: str) -> bool:
    name = _bare(canonical_field).lower()
    return name in _STATE_NAMES or name.endswith(_STATE_NAMES_SUFFIXES)


# Words a status/state column of an account, record or workflow plausibly
# holds. Deliberately generous: a hit means "looks like a state", and only a
# total miss on a small sample counts against the column.
_STATE_VOCABULARY = frozenset(
    """active inactive enabled disabled locked unlocked suspended deleted removed archived pending approved
    rejected declined open closed resolved unresolved draft complete completed incomplete failed failure success
    successful expired revoked terminated current historic new in_progress inprogress ongoing cancelled canceled
    submitted review reviewed assigned unassigned escalated overdue paused running stopped online offline
    healthy unhealthy compliant noncompliant non_compliant valid invalid true false yes no y n 0 1 on off
    live retired blocked allowed denied granted deprecated published unpublished verified unverified
    ok error warning critical high medium low none pass fail passed""".split()
)


def _state_tokens(value: str) -> set[str]:
    lowered = value.strip().lower()
    return {lowered, re.sub(r"[\s\-]+", "_", lowered)} | set(re.split(r"[\s_\-/]+", lowered))


# ---- what a column is ------------------------------------------------------
def column_kind_from_type(data_type: str | None) -> Kind | None:
    """Coarse kind from a discovered type string (SQLAlchemy `VARCHAR(255)`,
    `TIMESTAMP`, ... or the Mongo connector's `string`, `objectid`,
    `integer (optional)`, ...). None = unknown/unusable (JSON, arrays, mixed)."""
    if not data_type:
        return None
    t = re.sub(r"\(optional\)", "", data_type.lower()).strip()
    if not t or t.startswith("mixed") or t in {"null", "array", "object", "binary"}:
        return None
    if "interval" in t:
        return None
    if "uuid" in t or t in {"objectid", "uniqueidentifier"}:
        return "identifier"
    if "bool" in t or t in {"bit"}:
        return "boolean"
    if re.match(r"(timestamp|datetime|date|time|smalldatetime|datetimeoffset)", t):
        return "datetime"
    if re.match(r"((tiny|small|medium|big)?int|numeric|decimal|number|float|double|real|money|serial|bigserial|long)", t):
        return "numeric"
    if re.match(r"(varchar|nvarchar|char|nchar|text|string|clob|nclob|citext|character|longtext|mediumtext|tinytext)", t):
        return "text"
    return None


@dataclass(frozen=True)
class ColumnProfile:
    """What a sample of a column's values looked like. Hashable on purpose
    (top_values is a tuple) so it can sit inside cached scoring keys."""

    sample_size: int
    null_ratio: float
    distinct_count: int
    distinct_ratio: float
    value_kind: Kind | None = None
    top_values: tuple[str, ...] | None = None
    max_length: int | None = None


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    is_primary_key: bool = False
    data_type: str | None = None
    profile: ColumnProfile | None = None


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$")


def _value_kind_of(value: object) -> Kind:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float, Decimal)):
        return "numeric"
    if isinstance(value, (datetime, date)):
        return "datetime"
    if isinstance(value, uuid.UUID):
        return "identifier"
    text = str(value).strip()
    if _UUID_RE.match(text) or re.fullmatch(r"[0-9a-f]{24}", text):
        return "identifier"
    if _DATETIME_RE.match(text):
        return "datetime"
    if _NUMERIC_RE.match(text):
        return "numeric"
    if text.lower() in {"true", "false"}:
        return "boolean"
    return "text"


def may_store_values(
    field_name: str, is_sensitive: bool, distinct: Iterable[str], value_kind: str | None, present_count: int
) -> bool:
    """The one rule for when a profile may hold real values: only a small,
    ENUM-LIKE column whose NAME marks it as a category (status, type, level...),
    and never one that is sensitive, personal, an identifier, a number or a
    timestamp. Enum-like means the values REPEAT: a status has a handful of
    values spread over many rows, while a name or key column has about one value
    per row, however few rows a small table has. The cardinality cap alone cannot
    tell them apart (an 8-row users table has 8 user ids and 8 usernames), which is
    why the repetition test exists. `present_count` is the number of non-empty
    sampled values. Used both when the platform builds a profile itself and when it
    screens one a Gateway reported."""
    values = list(distinct)
    return (
        not is_sensitive
        and not is_pii_named(field_name)
        and not is_identifier_named(field_name)
        and is_categorical_named(field_name)
        and 0 < len(values) <= MAX_STORED_DISTINCT_VALUES
        and len(values) <= max(2, present_count // 2)
        and all(len(v) <= MAX_STORED_VALUE_LENGTH for v in values)
        and value_kind not in {"identifier", "datetime", "numeric"}
    )


_REPORTABLE_KINDS = frozenset({"identifier", "datetime", "boolean", "numeric", "text"})


def sanitize_reported_profile(field_name: str, is_sensitive: bool, reported: dict) -> ColumnProfile | None:
    """A Gateway computes its own profile inside the client's network, but
    the platform never trusts what arrives: numbers are clamped to sane
    ranges, the kind must be a known one, and `top_values` is dropped unless
    the same storage rule as build_column_profile allows real values for
    THIS column, judged against the platform's own is_sensitive flag (an
    auditor's decision, which a Gateway can't see or override), not the
    Gateway's word. None when the report is unusable."""
    try:
        sample_size = int(reported["sample_size"])
        distinct_count = int(reported["distinct_count"])
        null_ratio = float(reported["null_ratio"])
        distinct_ratio = float(reported["distinct_ratio"])
    except (KeyError, TypeError, ValueError):
        return None
    if sample_size < 1 or sample_size > 1_000_000:
        return None
    kind = reported.get("value_kind")
    value_kind = kind if kind in _REPORTABLE_KINDS else None

    top_values: tuple[str, ...] | None = None
    raw_values = reported.get("top_values")
    if isinstance(raw_values, list):
        cleaned = sorted({str(v).strip().lower() for v in raw_values if v is not None and str(v).strip() != ""})
        present_count = round(sample_size * (1.0 - min(max(null_ratio, 0.0), 1.0)))
        if may_store_values(field_name, is_sensitive, cleaned, value_kind, present_count):
            top_values = tuple(cleaned)

    max_length = reported.get("max_length")
    return ColumnProfile(
        sample_size=sample_size,
        null_ratio=round(min(max(null_ratio, 0.0), 1.0), 4),
        distinct_count=min(max(distinct_count, 0), sample_size),
        distinct_ratio=round(min(max(distinct_ratio, 0.0), 1.0), 4),
        value_kind=value_kind,  # type: ignore[arg-type]
        top_values=top_values,
        max_length=int(max_length) if isinstance(max_length, (int, float)) and max_length >= 0 else None,
    )


def build_column_profile(
    field_name: str, values: Sequence[object], *, is_sensitive: bool = False
) -> ColumnProfile | None:
    """Turn a sample of one column's values into a profile. None when there
    is no sample at all (an empty table tells us nothing)."""
    total = len(values)
    if total == 0:
        return None
    present = [v for v in values if v is not None and str(v).strip() != ""]
    null_ratio = round((total - len(present)) / total, 4)
    texts = [str(v).strip() for v in present]
    # Case variants of one value ("Active"/"active") are one value here.
    distinct = {t.lower() for t in texts}
    distinct_ratio = round(len(distinct) / len(present), 4) if present else 0.0

    value_kind: Kind | None = None
    if present:
        counts: dict[str, int] = {}
        for v in present:
            k = _value_kind_of(v)
            counts[k] = counts.get(k, 0) + 1
        top_kind, top_count = max(counts.items(), key=lambda kv: kv[1])
        # A clear majority names the column; a genuine mix stays unnamed
        # rather than guessing (the scorers then fall back to declared type).
        if top_count / len(present) >= 0.8:
            value_kind = top_kind  # type: ignore[assignment]

    top_values: tuple[str, ...] | None = None
    if may_store_values(field_name, is_sensitive, distinct, value_kind, len(present)):
        top_values = tuple(sorted(distinct))

    return ColumnProfile(
        sample_size=total,
        null_ratio=null_ratio,
        distinct_count=len(distinct),
        distinct_ratio=distinct_ratio,
        value_kind=value_kind,
        top_values=top_values,
        max_length=max((len(t) for t in texts), default=None),
    )


# ---- scoring ---------------------------------------------------------------
_COMPATIBLE = 100.0
_PLAUSIBLE = 65.0
_CONTRADICTION = 10.0

# canonical kind -> {column kind: score}. Anything not listed is a
# contradiction. "Plausible" = a common, sloppy-but-real way clients store it.
_TYPE_MATRIX: dict[str, dict[str, float]] = {
    "identifier": {"identifier": 100, "numeric": 100, "text": 100},
    "datetime": {"datetime": 100, "text": 65, "numeric": 55},
    "boolean": {"boolean": 100, "numeric": 70, "text": 60},
    "numeric": {"numeric": 100, "text": 55},
    "enum": {"text": 100, "enum": 100, "numeric": 70, "boolean": 70},
}


def _effective_column_kind(info_type: str | None, profile: ColumnProfile | None) -> Kind | None:
    declared = column_kind_from_type(info_type)
    observed = profile.value_kind if profile else None
    # What the values ARE beats what the type CLAIMS (a text column of ISO
    # dates is a datetime; a "varchar" of numbers is numeric).
    return observed or declared


def score_value_fit(
    canonical_field: str, *, data_type: str | None = None, profile: ColumnProfile | None = None
) -> tuple[float | None, str | None]:
    """0-100 (+ a plain-language reason when it isn't a good fit) for how well
    a column's type/values suit a canonical field. (None, None) = nothing
    checkable, which callers must treat as "no opinion", not "bad"."""
    kind = canonical_kind(canonical_field)
    if kind is None:
        return None, None
    column_kind = _effective_column_kind(data_type, profile)
    if column_kind is None and profile is None:
        return None, None

    score: float | None = None
    reason: str | None = None
    if column_kind is not None:
        score = _TYPE_MATRIX[kind].get(column_kind)
        if score is None:
            score = _CONTRADICTION
            reason = f"holds {column_kind} values, but {_bare(canonical_field)} should be {kind}-like"
        elif score < _COMPATIBLE:
            reason = f"stored as {column_kind}; {_bare(canonical_field)} is normally {kind}-like"

    if profile is not None and profile.sample_size >= 1:
        if kind == "identifier" and profile.null_ratio > 0.5 and profile.sample_size >= 5:
            score, reason = _CONTRADICTION, f"{round(profile.null_ratio * 100)}% empty, but {_bare(canonical_field)} should identify each row"
        elif kind == "enum" and profile.sample_size >= MIN_SAMPLE_FOR_ENUM_JUDGEMENT:
            # A status-like field has a handful of distinct values; a column
            # where nearly every row differs is free text or an identifier.
            if profile.distinct_ratio > 0.5 and profile.distinct_count > MAX_STORED_DISTINCT_VALUES:
                score = min(score if score is not None else 100.0, _CONTRADICTION)
                reason = f"{profile.distinct_count} different values in {profile.sample_size} rows — too varied for {_bare(canonical_field)}"
        if (
            kind == "enum"
            and is_state_field(canonical_field)
            and profile.sample_size >= MIN_SAMPLE_FOR_VOCABULARY_CHECK
            and profile.top_values
            and len(profile.top_values) >= 2
            and (score is None or score >= LOW_VALUE_FIT_THRESHOLD)
        ):
            hits = sum(1 for v in profile.top_values if _state_tokens(v) & _STATE_VOCABULARY)
            if hits == 0:
                shown = ", ".join(profile.top_values[:4])
                score = 25.0
                reason = f"values ({shown}) don't look like a {_bare(canonical_field)}"

    if score is None:
        return None, None
    return round(score, 2), (reason if score < _COMPATIBLE else None)


@dataclass(frozen=True)
class TableFit:
    structural: float | None
    value: float | None

    @property
    def effective(self) -> float | None:
        """The verdict callers act on: either signal saying "no" is enough,
        but the value signal only speaks when it had enough to go on."""
        if self.structural is None:
            return self.value
        if self.value is None:
            return self.structural
        return min(self.structural, self.value)


def _confident_in_object_matches(columns: Iterable[ColumnInfo], required_table_name: str) -> list[tuple[ColumnInfo, str]]:
    target = infer_object_for_entity(required_table_name)
    if target is None or target not in CANONICAL_MODEL:
        return []
    prefix = f"{target}."
    matches = []
    for column in columns:
        matched, score = suggest_canonical_field(
            column.name, is_primary_key=column.is_primary_key, preferred_object=target, in_object_only=True
        )
        if matched.startswith(prefix) and score >= 90.0:
            matches.append((column, matched))
    return matches


def score_table_value_fit(columns: Sequence[ColumnInfo], required_table_name: str) -> float | None:
    """Mean value fit over the table's confidently matched columns that have
    something checkable. None unless at least
    MIN_CHECKABLE_FIELDS_FOR_TABLE_VERDICT of them do — one odd column is a
    mapping question, not evidence the whole table is wrong."""
    scores = []
    for column, canonical_field in _confident_in_object_matches(columns, required_table_name):
        score, _ = score_value_fit(canonical_field, data_type=column.data_type, profile=column.profile)
        if score is not None:
            scores.append(score)
    if len(scores) < MIN_CHECKABLE_FIELDS_FOR_TABLE_VERDICT:
        return None
    return round(sum(scores) / len(scores), 2)


def score_table_fit(columns: Sequence[ColumnInfo], required_table_name: str) -> TableFit | None:
    """Structural (names) + value (types/samples) fit of one table against a
    required table name. None = nothing to judge at all (no columns, or no
    modeled object for that name)."""
    if not columns:
        return None
    structural = score_table_content_fit(
        [c.name for c in columns], required_table_name, frozenset(c.name for c in columns if c.is_primary_key)
    )
    if structural is None:
        return None
    return TableFit(structural=structural, value=score_table_value_fit(columns, required_table_name))
