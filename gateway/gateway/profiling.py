"""
Column profiling, computed here INSIDE the client's network so the platform
can check that a discovered column holds the right KIND of data (a `status`
column of account states, a `last_login` of timestamps) and not just that
its name looks right.

What leaves this machine is a small summary per column: how many rows were
sampled, how many were empty, how many distinct values, the kind of value.
Real values are included ONLY for a small, non-sensitive, enum-like column
(<= 20 distinct values, each <= 40 characters) — never for a column whose
name looks personal or secret (password, email, salary, ...), never for
identifiers or timestamps, and never for anything high-cardinality. The
platform screens every profile again on arrival and can only ever store less
than what is sent here, so a bug or a modified Gateway cannot widen it.

Deliberate duplicate of the platform's app/core/value_profile.py (this
package is deployed independently and cannot import from the backend). The
backend's test suite runs both implementations on the same inputs and fails
if they ever disagree — change them together.

Turn it off entirely with `profiling_enabled: false` in config.yaml, or per
connection with `profiling: false`.
"""
import logging
import re
import uuid
from datetime import date, datetime
from decimal import Decimal

logger = logging.getLogger("gateway.profiling")

MAX_STORED_DISTINCT_VALUES = 20
MAX_STORED_VALUE_LENGTH = 40

_PII_SUBSTRINGS = (
    "password", "passwd", "hash", "secret", "token", "credential", "apikey", "api_key", "passport",
    "national_id", "id_number", "email", "phone", "mobile", "address", "street", "postcode", "salary",
    "birth", "gender", "ethnic", "religion", "medical", "diagnos", "first_name", "last_name", "full_name",
    "surname", "account_number", "iban",
)
_PII_WORDS = frozenset("pwd salt ssn social tax mail cell fax zip pay wage bank card cvv pin dob health".split())

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$")

# Data types never worth pulling over the wire to profile.
_BINARY_TYPE_MARKERS = ("bytea", "blob", "binary", "image", "raw")


def is_pii_named(field_name: str) -> bool:
    lowered = re.sub(r"[^a-z0-9]+", "_", field_name.lower()).strip("_")
    if any(token in lowered for token in _PII_SUBSTRINGS):
        return True
    return any(word in _PII_WORDS for word in lowered.split("_"))


def _value_kind_of(value: object) -> str:
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


def build_column_profile(field_name: str, values: list, *, is_sensitive: bool = False) -> dict | None:
    """The wire shape the platform's DiscoveredFieldProfile expects. None when
    there is no sample at all (an empty table says nothing)."""
    total = len(values)
    if total == 0:
        return None
    present = [v for v in values if v is not None and str(v).strip() != ""]
    texts = [str(v).strip() for v in present]
    distinct = {t.lower() for t in texts}

    value_kind = None
    if present:
        counts: dict[str, int] = {}
        for v in present:
            kind = _value_kind_of(v)
            counts[kind] = counts.get(kind, 0) + 1
        top_kind, top_count = max(counts.items(), key=lambda kv: kv[1])
        if top_count / len(present) >= 0.8:
            value_kind = top_kind

    top_values = None
    if (
        not is_sensitive
        and not is_pii_named(field_name)
        and 0 < len(distinct) <= MAX_STORED_DISTINCT_VALUES
        and all(len(t) <= MAX_STORED_VALUE_LENGTH for t in distinct)
        and value_kind not in {"identifier", "datetime"}
    ):
        top_values = sorted(distinct)

    return {
        "sample_size": total,
        "null_ratio": round((total - len(present)) / total, 4),
        "distinct_count": len(distinct),
        "distinct_ratio": round(len(distinct) / len(present), 4) if present else 0.0,
        "value_kind": value_kind,
        "top_values": top_values,
        "max_length": max((len(t) for t in texts), default=None),
    }


def wanted_columns(fields: list[dict]) -> list[tuple[str, str | None]]:
    """(name, declared type) of the columns worth sampling — never a binary one."""
    return [
        (f["field_name"], f.get("data_type"))
        for f in fields
        if not any(marker in (f.get("data_type") or "").lower() for marker in _BINARY_TYPE_MARKERS)
    ]


def attach_profiles(connector, entities: list[dict], *, sample_rows: int) -> int:
    """Samples each discovered table through the connector and adds a
    `profile` to every column it could summarise. One table failing (dropped
    since discovery, no permission, timeout) never stops the rest. Returns
    how many tables were profiled."""
    profiled = 0
    for entity in entities:
        fields = entity.get("fields") or []
        columns = wanted_columns(fields)
        if not columns:
            continue
        try:
            rows = connector.sample_rows(entity["entity_name"], columns, sample_rows)
        except Exception:  # noqa: BLE001 — the reason can echo connection details; not worth logging in full
            logger.info("Could not sample %s; skipping its profile", entity["entity_name"])
            continue
        # Only columns that actually came back: a binary column was never
        # read, and a MongoDB field can be absent from every sampled document.
        sampled = {name for name, _ in columns}
        if rows:
            sampled &= set().union(*(row.keys() for row in rows))
        for field in fields:
            if field["field_name"] not in sampled:
                continue  # reporting a never-read column as all-empty would be a lie
            profile = build_column_profile(
                field["field_name"], [row.get(field["field_name"]) for row in rows], is_sensitive=bool(field.get("is_sensitive"))
            )
            if profile is not None:
                field["profile"] = profile
        profiled += 1
    return profiled
