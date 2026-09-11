"""
The Canonical Audit Data Model (product spec Section 19) — a fixed,
platform-wide taxonomy that every connector's discovered fields map into.
It is NOT a copy of any client's schema, and it is not tenant-editable
data, which is why it lives here as a versioned constant rather than a
database table: schema.sql's `test_data_mappings.canonical_field` is
already free text specifically so this list can grow without a migration.

Matching is a rule-based heuristic — abbreviation expansion + token overlap
+ table-name-based object inference — not machine learning. That is a
deliberate choice per the build instruction's Section 38 ("Do NOT
implement... Advanced AI... unless the core platform is stable"), and it is
good enough to separate "EMP_NO on an EMP_MASTER table is almost certainly
employee.employee_id" from "SUPV_FLAG could be almost anything," which is
the actual job of the confidence score.
"""
import difflib
import re

CANONICAL_MODEL: dict[str, list[str]] = {
    "employee": [
        "employee_id",
        "full_name",
        "employment_status",
        "termination_date",
        "hire_date",
        "department_id",
        "job_title",
    ],
    "user": ["user_id", "username", "status", "last_login", "employee_id"],
    "role": ["role_id", "role_name"],
    "privilege": ["privilege_id", "privilege_name"],
    "system": ["system_id", "system_name"],
    "login": ["login_id", "login_timestamp", "username"],
    "transaction": ["transaction_id", "transaction_date", "amount", "account"],
    "change": ["change_id", "change_date", "approved_by"],
    "vendor": ["vendor_id", "vendor_name", "bank_account"],
    "department": ["department_id", "department_name"],
    # CRM objects — added for the HubSpot connector, but engine-agnostic
    # like everything else here: any CRM (Salesforce, Zoho, ...) discovered
    # in the future maps into these same three, no connector-specific model.
    "customer": ["customer_id", "customer_name", "email", "phone", "company_name", "lifecycle_stage"],
    "deal": ["deal_id", "deal_name", "amount", "deal_stage", "close_date", "owner"],
    "company": ["company_id", "company_name", "industry", "domain", "annual_revenue"],
}

AUTO_ACCEPT_THRESHOLD = 90.0

# Common abbreviations seen in real client schemas (EMP_MASTER, TERM_DT, ...) —
# expanding these before comparing tokens is what separates a genuinely close
# match from an unrelated one.
_ABBREVIATIONS = {
    "emp": "employee",
    "no": "number",
    "num": "number",
    "nbr": "number",
    "stat": "status",
    "sts": "status",
    "dt": "date",
    "cd": "code",
    "dept": "department",
    "desc": "description",
    "addr": "address",
    "qty": "quantity",
    "amt": "amount",
    "txn": "transaction",
    "acct": "account",
    "supv": "supervisor",
    "mgr": "manager",
    "usr": "user",
    "pwd": "password",
    "ts": "timestamp",
    "master": "",
    "tbl": "",
    "flg": "flag",
}

_ID_LIKE_TOKENS = {"number", "id", "code"}


def _tokens(name: str) -> list[str]:
    raw = re.split(r"[^a-zA-Z0-9]+", name)
    expanded = [_ABBREVIATIONS.get(t.lower(), t.lower()) for t in raw if t]
    return [t for t in expanded if t]


def _token_overlap_score(source_tokens: set[str], target_tokens: set[str]) -> float:
    if not source_tokens or not target_tokens:
        return 0.0
    union = source_tokens | target_tokens
    overlap = source_tokens & target_tokens
    return len(overlap) / len(union) * 100


def infer_object_for_entity(entity_name: str) -> str | None:
    """Table names carry real signal — EMP_MASTER strongly implies the 'employee' object."""
    entity_tokens = set(_tokens(entity_name))
    best_object, best_score = None, 0.0
    for obj_name in CANONICAL_MODEL:
        overlap = entity_tokens & set(_tokens(obj_name))
        if overlap and len(overlap) > best_score:
            best_score = len(overlap)
            best_object = obj_name
    return best_object


def suggest_canonical_field(
    source_field_name: str, *, is_primary_key: bool = False, preferred_object: str | None = None
) -> tuple[str, float]:
    """Returns (best "object.field" match, confidence 0-100)."""
    source_tokens = set(_tokens(source_field_name))
    normalized_source = "".join(sorted(source_tokens))

    best_field = ""
    best_score = 0.0
    for obj_name, fields in CANONICAL_MODEL.items():
        for field_name in fields:
            target_tokens = set(_tokens(field_name))
            score = _token_overlap_score(source_tokens, target_tokens)

            normalized_target = "".join(sorted(target_tokens))
            seq_ratio = difflib.SequenceMatcher(None, normalized_source, normalized_target).ratio() * 100
            score = max(score, seq_ratio)

            # A table clearly named after this object gives every field in it a
            # meaningful head start when scoring against that object's fields.
            if preferred_object == obj_name:
                score = min(100.0, score + 25)

            # A primary-key column with an id-like token (NO/CODE/NUM) mapping to
            # this object's own "_id" field is the single strongest real-world signal.
            if is_primary_key and field_name.endswith("_id") and (source_tokens & _ID_LIKE_TOKENS):
                candidate = 96.0 if preferred_object == obj_name else 75.0
                score = max(score, candidate)

            if score > best_score:
                best_score = score
                best_field = f"{obj_name}.{field_name}"

    return best_field, round(best_score, 2)


def mapping_status_for_confidence(confidence: float) -> str:
    return "auto" if confidence >= AUTO_ACCEPT_THRESHOLD else "needs_review"
