import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from datetime import datetime, timezone

from app.models.audit_test import AuditTest, ControlAuditTest, TestRule
from app.models.control_library import ControlLibraryEntry
from app.models.evidence_exception import Exception_, ExceptionRecord
from app.models.monitoring import MonitoringSchedule, TestExecution
from app.models.organization import OrganizationSetting
from app.models.risk_control import Control, Risk, RiskControl
from app.services.audit_log_service import log_action

_CLOSING_STATUSES = ("resolved", "closed")
SOD_EXCEPTION_CLOSURE_SETTING = "sod_required_for_exception_closure"


def sod_required(db: Session, *, organization_id: uuid.UUID) -> bool:
    """
    Opt-in per organization via the existing organization_settings table
    (no new table needed) — defaults to off. A hard-coded global rule would
    immediately block a single person testing/demoing the whole platform
    solo (creating, owning, and closing their own test exception is exactly
    that workflow); clients who want the real control get to turn it on.

    Reused beyond exception closure itself — finding_service.create_retest
    checks the same flag to enforce remediation performer != verifier,
    since both are "does this org want a second person on closure-type
    actions" and a second, separate setting would just be two knobs
    controlling the same underlying policy question.
    """
    setting = db.scalar(
        select(OrganizationSetting).where(
            OrganizationSetting.organization_id == organization_id,
            OrganizationSetting.setting_name == SOD_EXCEPTION_CLOSURE_SETTING,
        )
    )
    return setting is not None and setting.setting_value == "true"

# An exception in any of these statuses is still "the same ongoing issue" —
# a fresh detection of it updates that row instead of inserting a new one.
# 'resolved'/'closed' are deliberately excluded: if the issue recurs after
# being marked fixed, that recurrence is a genuinely new exception, not a
# continuation — auto-merging it back in would let a real regression hide
# behind an old, already-closed record.
OPEN_STATUSES = ("open", "awaiting_evidence", "in_progress")


def find_open_exception(db: Session, *, audit_test_id: uuid.UUID, description: str) -> Exception_ | None:
    return db.scalar(
        select(Exception_)
        .join(TestExecution, TestExecution.execution_id == Exception_.execution_id)
        .where(
            TestExecution.audit_test_id == audit_test_id,
            Exception_.exception_description == description,
            Exception_.status.in_(OPEN_STATUSES),
        )
        .order_by(Exception_.last_detected_at.desc())
        .limit(1)
    )


# A plain-English fallback per rule shape, used whenever an exception has
# no more specific recommended_remediation of its own — that field exists
# on the model but nothing populates it yet (see Exception_.recommended_
# remediation), so this is the real source of "what to do" today.
_RULE_TYPE_RECOMMENDATION = {
    "threshold": "Look at this record and check it against your policy. Then fix it, get it approved, or mark it as OK.",
    "duplicate": "Look at these matching records together. If one is a mistake, merge them or remove the extra one.",
    "missing_match": "Find out why the link is missing. If it should exist, add it. If it shouldn't, find out why this record is here at all.",
    "cross_match_condition": "Look at both records together and fix whichever one is wrong — usually by turning off access, changing a status, or getting the missing approval.",
    "three_way_match": "Compare all three records side by side (for example, the order, the delivery, and the invoice) and fix whichever one doesn't match the other two.",
    "four_way_match": "Trace the chain of records (for example, the transaction, its approval, the approver's role, and that role's limit) and fix whichever link is wrong.",
    "balance": "Add up every line in this group and find which one is missing, wrong, or extra — the debit and credit totals don't match.",
    "baseline_comparison": "Compare this record's setting to the approved baseline and either fix the setting or get an exception approved.",
    "reconciliation": "Compare this ledger balance to the underlying transactions and find which one is missing, wrong, or extra.",
}


def _humanize_field_name(key: str) -> str:
    """Turns a raw field name like 'employment_status_primary' into
    'Employment Status' — strips the _primary/_secondary/_tertiary/
    _quaternary role suffix the rule engine adds, then title-cases each word."""
    for suffix in ("_primary", "_secondary", "_tertiary", "_quaternary"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
            break
    words = key.replace("_", " ").split()
    return " ".join(word.upper() if word.lower() == "id" else word.capitalize() for word in words)


_OPERATOR_WORDS = {
    "eq": "is", "ne": "is not", "gt": "is greater than", "gte": "is at least",
    "lt": "is less than", "lte": "is at most", "is_null": "is empty",
    "is_not_null": "is not empty", "matches": "matches the pattern",
    "in": "is one of", "not_in": "is none of",
}


def _humanize_object(name: str) -> str:
    """Canonical object names are inconsistently already-plural (a
    'certificates' collection vs. a singular 'employee') since they mirror
    physical table/collection names — but a sentence describing ONE record
    ("Certificate CRT002...") needs the singular either way. A trailing 's'
    is stripped unless the word ends 'ss'/'us'/'is' (access, status,
    analysis — words a naive strip would mangle into nonsense)."""
    label = name.replace("_", " ")
    if label.endswith("s") and not label.endswith(("ss", "us", "is")):
        label = label[:-1]
    return label


def _fact_value(exception_data: dict, field: str, role: str | None):
    """A cross_match_condition/three_way_match exception's data suffixes a
    field with _primary/_secondary/_tertiary only when the SAME field name
    exists on both sides of the join (see rule_evaluation.py's overlap_keys
    logic) — never unconditionally. Try the role-suffixed key first, then
    the bare field name, so a real value is found either way."""
    if role and f"{field}_{role}" in exception_data:
        return exception_data[f"{field}_{role}"]
    return exception_data.get(field)


def _describe_literal_condition(field: str, operator: str, value) -> str:
    """Like _describe_fact, but for a condition where no per-record value
    exists to show at all — a missing_match secondary_condition describes
    what a WOULD-BE match would have needed to satisfy, on a row that, by
    definition, was never found (there is nothing in exception_data to
    look up), so this always falls back to the rule's own literal."""
    label = _humanize_field_name(field).lower()
    if operator in ("is_null", "is_not_null"):
        return f"{label} {_OPERATOR_WORDS[operator]}"
    return f"{label} {_OPERATOR_WORDS.get(operator, operator)} {value}"


def _describe_dynamic_relative_date_fact(exception_data: dict, date_field: str, date_role: str | None, operator: str, offset_field: str, offset_role: str | None, direction: int) -> str:
    """'its run at (2026-08-08) is less than the allowed 30-day window' —
    the dynamic-relative-date counterpart of _describe_fact: both the date
    and the day-count are real per-record values from THIS joined pair
    (unlike a missing_match secondary_condition, this rule_type only ever
    fires on an actual match, so both sides are present in exception_data)."""
    date_label = _humanize_field_name(date_field).lower()
    date_val = _fact_value(exception_data, date_field, date_role)
    offset_val = _fact_value(exception_data, offset_field, offset_role)
    window = "before" if direction == -1 else "after"
    return f"its {date_label} ({date_val}) {_OPERATOR_WORDS.get(operator, operator)} the {offset_val}-day {window} cutoff from now"


def _describe_fact(exception_data: dict, field: str, role: str | None, operator: str, rule_value) -> str:
    """'employment status is terminated' — the field's real value on this
    specific record when it's actually present in the data (informative
    for a range/date comparison, e.g. an exact last-login date rather than
    just the 90-day threshold it failed), falling back to the rule's own
    literal only when the field wasn't carried through to exception_data at
    all (duplicate/missing_match's pre-filter conditions, which describe
    the CANDIDATE set rather than a per-record fact)."""
    label = _humanize_field_name(field).lower()
    if operator in ("is_null", "is_not_null"):
        return f"{label} {_OPERATOR_WORDS[operator]}"
    actual = _fact_value(exception_data, field, role)
    shown = actual if actual is not None else rule_value
    return f"{label} {_OPERATOR_WORDS.get(operator, operator)} {shown}"


def _natural_summary(rule_definition: dict | None, exception_data: dict, record_identifier: str | None, control_label: str) -> str | None:
    """A rule-shape-aware sentence built from the SAME exception_data every
    control already produces (never hand-written per control — there are
    157 of them) — e.g. "Employee EMP007: employment status is terminated,
    but its linked user still shows status as active" instead of the
    generic "Record EMP007 did not pass the check." Returns None when the
    rule can't be loaded or its shape isn't one of the five primitives, so
    the caller falls back to the generic line rather than showing nothing."""
    if not rule_definition or not exception_data:
        return None
    rule_type = rule_definition.get("rule_type")
    ident = record_identifier or "This record"

    if rule_type == "cross_match_condition":
        primary_obj = _humanize_object(rule_definition["primary_object"])
        secondary_obj = _humanize_object(rule_definition["secondary_object"])
        cp, cs = rule_definition["condition_primary"], rule_definition["condition_secondary"]
        primary_fact = _describe_fact(exception_data, cp["field"], "primary", cp["operator"], cp.get("value"))
        secondary_fact = _describe_fact(exception_data, cs["field"], "secondary", cs["operator"], cs.get("value"))
        sentence = f"{primary_obj.capitalize()} {ident}: {primary_fact}, but its linked {secondary_obj}'s {secondary_fact}."
        dc = rule_definition.get("dynamic_relative_date_comparison")
        if dc is not None:
            extra = _describe_dynamic_relative_date_fact(
                exception_data, dc["primary_field"], "primary", dc["operator"], dc["secondary_field"], "secondary", dc.get("direction", -1)
            )
            sentence = f"{sentence[:-1]}; also, {extra}."
        return sentence

    if rule_type == "threshold":
        obj = _humanize_object(rule_definition["object"])
        fact = _describe_fact(exception_data, rule_definition["field"], None, rule_definition["operator"], rule_definition.get("value"))
        return f"{obj.capitalize()} {ident}: {fact}."

    if rule_type == "missing_match":
        primary_obj = _humanize_object(rule_definition["primary_object"])
        secondary_obj = _humanize_object(rule_definition["secondary_object"])
        secondary_condition = rule_definition.get("secondary_condition")
        if secondary_condition is not None:
            condition_text = _describe_literal_condition(secondary_condition["field"], secondary_condition["operator"], secondary_condition.get("value"))
            return f"{primary_obj.capitalize()} {ident} has no matching {secondary_obj} record where {condition_text}."
        return f"{primary_obj.capitalize()} {ident} has no matching {secondary_obj} record at all."

    if rule_type == "duplicate":
        obj = _humanize_object(rule_definition["object"])
        group_by = ", ".join(_humanize_field_name(f).lower() for f in rule_definition["group_by"])
        distinct_field = rule_definition.get("distinct_field")
        if distinct_field:
            distinct_label = _humanize_field_name(distinct_field).lower()
            return f"This {obj} record ({ident}) has the same {group_by} as other {obj} records with a different {distinct_label}."
        return f"This {obj} record ({ident}) shares the same {group_by} with at least one other {obj} record."

    if rule_type == "balance":
        obj = _humanize_object(rule_definition["object"])
        group_by = ", ".join(_humanize_field_name(f).lower() for f in rule_definition["group_by"])
        debit_label = _humanize_field_name(rule_definition["debit_field"]).lower()
        credit_label = _humanize_field_name(rule_definition["credit_field"]).lower()
        return f"This {obj} record ({ident}) belongs to a {group_by} whose total {debit_label} does not equal its total {credit_label}."

    if rule_type == "baseline_comparison":
        obj = _humanize_object(rule_definition["object"])
        baseline_obj = _humanize_object(rule_definition["baseline_object"])
        field_label = _humanize_field_name(rule_definition["field"]).lower()
        return f"This {obj} record ({ident})'s {field_label} does not meet the {baseline_obj} baseline."

    if rule_type == "reconciliation":
        ledger_obj = _humanize_object(rule_definition["ledger_object"])
        subledger_obj = _humanize_object(rule_definition["subledger_object"])
        return f"This {ledger_obj} record ({ident}) does not reconcile to its {subledger_obj} total."

    if rule_type == "three_way_match":
        primary_obj = _humanize_object(rule_definition["primary_object"])
        secondary_obj = _humanize_object(rule_definition["secondary_object"])
        tertiary_obj = _humanize_object(rule_definition["tertiary_object"])
        role_obj = {"primary": primary_obj, "secondary": secondary_obj, "tertiary": tertiary_obj}
        parts = []
        for role, obj_label in (("primary", primary_obj), ("secondary", secondary_obj), ("tertiary", tertiary_obj)):
            cond = rule_definition.get(f"condition_{role}")
            if cond:
                parts.append(f"its {obj_label} {_describe_fact(exception_data, cond['field'], role, cond['operator'], cond.get('value'))}")
        fc = rule_definition.get("field_comparison")
        if fc is not None:
            left_label = _humanize_field_name(fc["left_field"]).lower()
            right_label = _humanize_field_name(fc["right_field"]).lower()
            left_val = _fact_value(exception_data, fc["left_field"], fc["left_object"])
            right_val = _fact_value(exception_data, fc["right_field"], fc["right_object"])
            parts.append(
                f"its {role_obj[fc['left_object']]}'s {left_label} ({left_val}) {_OPERATOR_WORDS.get(fc['operator'], fc['operator'])} "
                f"its {role_obj[fc['right_object']]}'s {right_label} ({right_val})"
            )
        dc = rule_definition.get("dynamic_relative_date_comparison")
        if dc is not None:
            parts.append(
                _describe_dynamic_relative_date_fact(
                    exception_data, dc["date_field"], dc["date_object"], dc["operator"], dc["offset_field"], dc["offset_object"], dc.get("direction", -1)
                )
            )
        joined = "; ".join(parts) if parts else f"its linked {primary_obj}, {secondary_obj}, and {tertiary_obj} records don't reconcile"
        return f"{ident}: {joined}."

    if rule_type == "four_way_match":
        primary_obj = _humanize_object(rule_definition["primary_object"])
        secondary_obj = _humanize_object(rule_definition["secondary_object"])
        tertiary_obj = _humanize_object(rule_definition["tertiary_object"])
        quaternary_obj = _humanize_object(rule_definition["quaternary_object"])
        role_obj = {"primary": primary_obj, "secondary": secondary_obj, "tertiary": tertiary_obj, "quaternary": quaternary_obj}
        parts = []
        for role, obj_label in (
            ("primary", primary_obj), ("secondary", secondary_obj), ("tertiary", tertiary_obj), ("quaternary", quaternary_obj),
        ):
            cond = rule_definition.get(f"condition_{role}")
            if cond:
                parts.append(f"its {obj_label} {_describe_fact(exception_data, cond['field'], role, cond['operator'], cond.get('value'))}")
        fc = rule_definition.get("field_comparison")
        if fc is not None:
            left_label = _humanize_field_name(fc["left_field"]).lower()
            right_label = _humanize_field_name(fc["right_field"]).lower()
            left_val = _fact_value(exception_data, fc["left_field"], fc["left_object"])
            right_val = _fact_value(exception_data, fc["right_field"], fc["right_object"])
            parts.append(
                f"its {role_obj[fc['left_object']]}'s {left_label} ({left_val}) {_OPERATOR_WORDS.get(fc['operator'], fc['operator'])} "
                f"its {role_obj[fc['right_object']]}'s {right_label} ({right_val})"
            )
        dc = rule_definition.get("dynamic_relative_date_comparison")
        if dc is not None:
            parts.append(
                _describe_dynamic_relative_date_fact(
                    exception_data, dc["date_field"], dc["date_object"], dc["operator"], dc["offset_field"], dc["offset_object"], dc.get("direction", -1)
                )
            )
        joined = (
            "; ".join(parts)
            if parts
            else f"its linked {primary_obj}, {secondary_obj}, {tertiary_obj}, and {quaternary_obj} records don't reconcile"
        )
        return f"{ident}: {joined}."

    return None


def get_control_for_audit_test(db: Session, *, audit_test_id: uuid.UUID) -> Control | None:
    """The control an audit test belongs to, if any — a manually-created
    test with no control_audit_tests link (or no control_library entry)
    returns None, same as explain_exception already tolerates."""
    link = db.scalar(select(ControlAuditTest).where(ControlAuditTest.audit_test_id == audit_test_id))
    if link is None:
        return None
    return db.get(Control, link.control_id)


def get_controls_for_audit_tests_bulk(db: Session, *, audit_test_ids: set[uuid.UUID]) -> dict[uuid.UUID, Control]:
    """Batched form of get_control_for_audit_test — one joined query for
    every audit_test_id at once instead of two round trips PER id. A list
    with hundreds of rows sharing a much smaller set of underlying tests
    (e.g. Evidence, one row per execution of the same handful of tests)
    turned calling get_control_for_audit_test per row into hundreds of
    redundant queries for identical answers; see list_evidence_for_
    organization, which hit exactly this."""
    if not audit_test_ids:
        return {}
    result: dict[uuid.UUID, Control] = {}
    for link, control in db.execute(
        select(ControlAuditTest, Control)
        .join(Control, Control.control_id == ControlAuditTest.control_id)
        .where(ControlAuditTest.audit_test_id.in_(audit_test_ids))
    ):
        result.setdefault(link.audit_test_id, control)
    return result


def explain_exceptions_bulk(
    db: Session,
    *,
    exceptions: list[Exception_],
    executions: dict[uuid.UUID, TestExecution] | None = None,
    audit_tests: dict[uuid.UUID, AuditTest] | None = None,
    rules: dict[uuid.UUID, TestRule] | None = None,
) -> dict[uuid.UUID, dict]:
    """Same output as explain_exception, for many exceptions in one call —
    a handful of batch queries total instead of ~8 sequential round trips
    PER exception. list_exceptions_for_organization can return hundreds of
    rows on a single page load; calling explain_exception once per row (the
    original approach) turned that into thousands of queries, which is
    what actually made the Exceptions/Executions pages slow to populate —
    the data was correct, it just took a while to arrive.

    executions/audit_tests can be passed in already keyed by id when the
    caller's own query already joined them (list_exceptions_for_organization
    joins both to filter by organization) — that skips two more round trips
    that would otherwise just re-fetch rows the caller already has."""
    if not exceptions:
        return {}

    if executions is None:
        execution_ids = {exc.execution_id for exc in exceptions}
        executions = {e.execution_id: e for e in db.scalars(select(TestExecution).where(TestExecution.execution_id.in_(execution_ids)))}

    if audit_tests is None:
        audit_test_ids = {e.audit_test_id for e in executions.values()}
        audit_tests = (
            {a.audit_test_id: a for a in db.scalars(select(AuditTest).where(AuditTest.audit_test_id.in_(audit_test_ids)))}
            if audit_test_ids
            else {}
        )
    else:
        audit_test_ids = set(audit_tests.keys())

    if rules is None:
        rule_ids = {e.rule_id for e in executions.values() if e.rule_id}
        rules = {r.rule_id: r for r in db.scalars(select(TestRule).where(TestRule.rule_id.in_(rule_ids)))} if rule_ids else {}

    # get_control_for_audit_test does 2 queries (link, then control) per
    # audit test; here it's one joined query for ALL of them, plus the
    # control's library entry riding along in the same round trip (an
    # outer join since not every control has one) — keeping the original
    # "first link wins" rule for an audit test with more than one control.
    control_id_by_audit_test: dict[uuid.UUID, uuid.UUID] = {}
    controls: dict[uuid.UUID, Control] = {}
    library_entries: dict[uuid.UUID, ControlLibraryEntry] = {}
    if audit_test_ids:
        for link, control, library_entry in db.execute(
            select(ControlAuditTest, Control, ControlLibraryEntry)
            .join(Control, Control.control_id == ControlAuditTest.control_id)
            .outerjoin(ControlLibraryEntry, ControlLibraryEntry.control_library_id == Control.control_library_id)
            .where(ControlAuditTest.audit_test_id.in_(audit_test_ids))
        ):
            control_id_by_audit_test.setdefault(link.audit_test_id, link.control_id)
            controls.setdefault(control.control_id, control)
            if library_entry is not None:
                library_entries.setdefault(library_entry.control_library_id, library_entry)

    control_ids = set(control_id_by_audit_test.values())

    # Same idea: RiskControl + Risk in one joined query instead of two.
    risk_id_by_control: dict[uuid.UUID, uuid.UUID] = {}
    risks: dict[uuid.UUID, Risk] = {}
    if control_ids:
        for link, risk in db.execute(
            select(RiskControl, Risk)
            .join(Risk, Risk.risk_id == RiskControl.risk_id)
            .where(RiskControl.control_id.in_(control_ids))
        ):
            risk_id_by_control.setdefault(link.control_id, link.risk_id)
            risks.setdefault(risk.risk_id, risk)

    exception_ids = [exc.exception_id for exc in exceptions]
    records_by_exception: dict[uuid.UUID, list[ExceptionRecord]] = {}
    for record in db.scalars(select(ExceptionRecord).where(ExceptionRecord.exception_id.in_(exception_ids))):
        records_by_exception.setdefault(record.exception_id, []).append(record)

    explanations: dict[uuid.UUID, dict] = {}
    for exception in exceptions:
        execution = executions.get(exception.execution_id)
        audit_test = audit_tests.get(execution.audit_test_id) if execution else None
        rule = rules.get(execution.rule_id) if execution and execution.rule_id else None

        control = controls.get(control_id_by_audit_test.get(audit_test.audit_test_id)) if audit_test else None
        library_entry = library_entries.get(control.control_library_id) if control and control.control_library_id else None
        risk = risks.get(risk_id_by_control.get(control.control_id)) if control else None

        records = records_by_exception.get(exception.exception_id, [])
        facts = [
            {"label": _humanize_field_name(key), "value": "(no value)" if value is None else str(value)}
            for key, value in (records[0].exception_data or {}).items()
        ] if records else []

        control_label = f"{control.control_code} — {control.control_name}" if control else (audit_test.test_name if audit_test else "This control")
        # exception.exception_description is an internal grouping key ("{test
        # name}: exception on {record id}", set by execution_service so repeat
        # detections of the SAME record update one row instead of piling up
        # duplicates) — not written to be read aloud.
        record_id = records[0].record_identifier if records else None
        rule_definition = json.loads(rule.rule_definition) if rule else None
        natural = _natural_summary(rule_definition, (records[0].exception_data or {}) if records else {}, record_id, control_label)
        summary = natural or (
            f"Record {record_id} did not pass the \"{control_label}\" check."
            if record_id
            else f"A record did not pass the \"{control_label}\" check."
        )

        why_it_matters = (
            risk.risk_description
            if risk is not None and risk.risk_description
            else (
                f"This control exists to check: {library_entry.audit_procedure}"
                if library_entry is not None
                else "This check exists to catch a real problem — when it fails, it usually means something needs a closer look."
            )
        )

        what_to_do = exception.recommended_remediation or _RULE_TYPE_RECOMMENDATION.get(
            rule.rule_type if rule else None, "Look into this record and decide what needs to change."
        )

        explanations[exception.exception_id] = {
            "summary": summary,
            "facts": facts,
            "why_it_matters": why_it_matters,
            "what_to_do": what_to_do,
            "seen_count": exception.occurrence_count,
            "first_seen": exception.detected_at,
            "last_seen": exception.last_detected_at,
            "control_code": control.control_code if control else None,
            "control_name": control.control_name if control else None,
            # Lets a reader match this exception to "the current status of
            # THIS test" by audit_test_id rather than by the exact execution_id
            # it happens to be pointed at right now — more robust for a
            # frequently re-running test, where execution_id moves forward on
            # every re-detection (see execution_service.record_execution_report)
            # and a client fetching executions/exceptions as two separate
            # requests can otherwise catch them a beat apart.
            "audit_test_id": audit_test.audit_test_id if audit_test else None,
        }
    return explanations


def explain_exception(db: Session, *, exception: Exception_) -> dict:
    """Everything a non-technical reader needs to understand one exception:
    what was actually found (the real field values from the record that
    failed), why it matters (the real, linked risk this control exists to
    catch — not a generic warning), and what to do about it. Built on the
    fly from data that already exists (exception_records, the control's
    own audit_procedure, its linked risk's risk_description) rather than
    hand-written per control, so it stays accurate as mappings/data change
    and covers all 157 controls without anyone writing 157 explanations.
    Single-exception convenience wrapper around explain_exceptions_bulk,
    used by the dedicated GET .../explanation endpoint."""
    return explain_exceptions_bulk(db, exceptions=[exception])[exception.exception_id]


def _trigger_immediate_retest(db: Session, *, exception: Exception_) -> None:
    """Marking an exception resolved/closed should prove it, not just say
    it — bump the underlying test's active schedule so the next Gateway
    (or direct-execution) poll picks it up right away instead of waiting
    for its normal cadence. Same 'eligible immediately' mechanism
    monitoring_service.approve_schedule already uses; a no-op if the test
    has no active schedule (a device/software-compliance control has none
    at all — those re-evaluate on their own whenever telemetry arrives)."""
    execution = db.get(TestExecution, exception.execution_id)
    if execution is None:
        return
    schedule = db.scalar(
        select(MonitoringSchedule).where(
            MonitoringSchedule.audit_test_id == execution.audit_test_id, MonitoringSchedule.status == "active"
        )
    )
    if schedule is not None:
        schedule.next_run = datetime.now(timezone.utc)


def update_exception(
    db: Session, *, exception: Exception_, status: str | None, owner_id: uuid.UUID | None, clear_owner: bool = False,
    organization_id: uuid.UUID, updated_by_user_id: uuid.UUID,
) -> Exception_:
    old_status = exception.status
    if (
        status in _CLOSING_STATUSES
        and exception.owner_id is not None
        and exception.owner_id == updated_by_user_id
        and sod_required(db, organization_id=organization_id)
    ):
        raise ValueError(
            "Segregation of duties: the assigned owner of this exception cannot also close it — "
            "have another authorized user review and close it instead."
        )
    if status is not None:
        exception.status = status
    if owner_id is not None:
        exception.owner_id = owner_id
    elif clear_owner:
        # owner_id=None alone can't tell "the caller didn't touch this
        # field" apart from "the caller explicitly wants it cleared" —
        # clear_owner is that explicit signal (see routes/exceptions.py's
        # use of payload.model_fields_set), otherwise "Unassigned" in the
        # owner dropdown could never actually take effect.
        exception.owner_id = None
    if status in _CLOSING_STATUSES and old_status != status:
        _trigger_immediate_retest(db, exception=exception)
    log_action(
        db,
        action=f"Exception status changed: {old_status} -> {exception.status}",
        organization_id=organization_id,
        user_id=updated_by_user_id,
        entity_type="exceptions",
        entity_id=exception.exception_id,
        old_value={"status": old_status},
        new_value={"status": exception.status, "owner_id": str(exception.owner_id) if exception.owner_id else None},
    )
    db.commit()
    db.refresh(exception)
    return exception
