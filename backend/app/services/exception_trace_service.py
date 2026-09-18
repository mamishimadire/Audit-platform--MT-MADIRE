import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_test import AuditTest, TestDataMapping, TestRule
from app.models.data_source import DataEntity
from app.models.evidence_exception import Exception_, ExceptionRecord
from app.models.monitoring import TestExecution
from app.schemas.audit_engine import ExceptionTraceOut, TraceFieldOut, TraceObjectOut
from app.services.exception_service import _humanize_field_name, _humanize_object, _natural_summary, get_control_for_audit_test

# Every rule_type's canonical objects, in role order — mirrors
# exception_service._natural_summary's own branching, since this is the
# same rule-shape knowledge applied to a different question ("which
# physical table did each value come from" instead of "what sentence
# describes this").
_SINGLE_OBJECT_TYPES = ("threshold", "duplicate", "balance")
_TWO_OBJECT_TYPES = ("missing_match", "cross_match_condition")
_THREE_OBJECT_TYPES = ("three_way_match",)
_FOUR_OBJECT_TYPES = ("four_way_match",)


def _object_roles(rule_definition: dict) -> list[tuple[str | None, str]]:
    rule_type = rule_definition.get("rule_type")
    if rule_type in _SINGLE_OBJECT_TYPES:
        return [(None, rule_definition["object"])]
    if rule_type in _TWO_OBJECT_TYPES:
        return [("primary", rule_definition["primary_object"]), ("secondary", rule_definition["secondary_object"])]
    if rule_type in _THREE_OBJECT_TYPES:
        return [
            ("primary", rule_definition["primary_object"]),
            ("secondary", rule_definition["secondary_object"]),
            ("tertiary", rule_definition["tertiary_object"]),
        ]
    if rule_type in _FOUR_OBJECT_TYPES:
        return [
            ("primary", rule_definition["primary_object"]),
            ("secondary", rule_definition["secondary_object"]),
            ("tertiary", rule_definition["tertiary_object"]),
            ("quaternary", rule_definition["quaternary_object"]),
        ]
    return []


def _physical_table_name(db: Session, *, audit_test_id: uuid.UUID, canonical_object: str) -> str | None:
    mapping = db.scalar(
        select(TestDataMapping).where(
            TestDataMapping.audit_test_id == audit_test_id,
            TestDataMapping.mapping_status == "approved",
            TestDataMapping.canonical_field.like(f"{canonical_object}.%"),
        )
    )
    if mapping is None:
        return None
    entity = db.get(DataEntity, mapping.entity_id)
    return entity.entity_name if entity else None


def _canonical_field_names(db: Session, *, audit_test_id: uuid.UUID, canonical_object: str) -> set[str]:
    """Every canonical field name approved for this object on this test —
    used to attribute an UNSUFFIXED exception_data key to the one role
    whose known fields it matches, when a rule genuinely merges more than
    one object's raw fields into one flat dict (see rule_evaluation.py's
    cross_match_condition/three_way_match — a field is only suffixed
    _primary/_secondary/_tertiary when the SAME name exists on both sides
    of the join; an unsuffixed field is unambiguous exactly because it
    doesn't collide, so its bare name still identifies which object's
    approved mapping it belongs to)."""
    rows = db.scalars(
        select(TestDataMapping.canonical_field).where(
            TestDataMapping.audit_test_id == audit_test_id,
            TestDataMapping.mapping_status == "approved",
            TestDataMapping.canonical_field.like(f"{canonical_object}.%"),
        )
    )
    return {field.split(".", 1)[1] for field in rows if field and "." in field}


def _format_value(value) -> str:
    if value is None:
        return "(no value)"
    return str(value)


def trace_from_exception(db: Session, *, exception: Exception_) -> ExceptionTraceOut:
    """The 'why did the system reach this conclusion' view — the control,
    the exact test run, and every physical table/field value the rule
    actually compared to produce this exception. Unlike trace_from_finding
    (which walks a Finding UP to its business process), this walks an
    Exception DOWN into the data behind it."""
    execution = db.get(TestExecution, exception.execution_id)
    audit_test = db.get(AuditTest, execution.audit_test_id) if execution else None
    rule = db.get(TestRule, execution.rule_id) if execution and execution.rule_id else None
    rule_definition = json.loads(rule.rule_definition) if rule else None

    control = get_control_for_audit_test(db, audit_test_id=audit_test.audit_test_id) if audit_test else None
    records = list(db.scalars(select(ExceptionRecord).where(ExceptionRecord.exception_id == exception.exception_id)))
    exception_data = (records[0].exception_data or {}) if records else {}
    record_id = records[0].record_identifier if records else None

    roles = _object_roles(rule_definition) if rule_definition else []
    objects: list[TraceObjectOut] = []
    consumed_keys: set[str] = set()

    if len(roles) == 1:
        role, canonical_object = roles[0]
        fields = [TraceFieldOut(field=_humanize_field_name(k), value=_format_value(v)) for k, v in exception_data.items()]
        consumed_keys.update(exception_data.keys())
        objects.append(
            TraceObjectOut(
                role=role,
                canonical_object=canonical_object,
                table_name=_physical_table_name(db, audit_test_id=audit_test.audit_test_id, canonical_object=canonical_object) if audit_test else None,
                fields=fields,
            )
        )
    elif rule_definition and rule_definition.get("rule_type") == "missing_match":
        # exception_data is the WHOLE primary record — there is, by
        # definition, no matching secondary row to show any data for.
        primary_role, primary_object = roles[0]
        fields = [TraceFieldOut(field=_humanize_field_name(k), value=_format_value(v)) for k, v in exception_data.items()]
        consumed_keys.update(exception_data.keys())
        objects.append(
            TraceObjectOut(
                role=primary_role,
                canonical_object=primary_object,
                table_name=_physical_table_name(db, audit_test_id=audit_test.audit_test_id, canonical_object=primary_object) if audit_test else None,
                fields=fields,
            )
        )
        secondary_role, secondary_object = roles[1]
        objects.append(
            TraceObjectOut(
                role=secondary_role,
                canonical_object=secondary_object,
                table_name=_physical_table_name(db, audit_test_id=audit_test.audit_test_id, canonical_object=secondary_object) if audit_test else None,
                fields=[TraceFieldOut(field="(no matching record)", value="—")],
            )
        )
    elif len(roles) > 1 and audit_test is not None:
        # cross_match_condition / three_way_match: exception_data merges
        # more than one object's raw fields — suffixed on collision, bare
        # otherwise. Bare keys are attributed by matching them against
        # each role's own approved canonical field names.
        role_field_names = {role: _canonical_field_names(db, audit_test_id=audit_test.audit_test_id, canonical_object=obj) for role, obj in roles}
        role_buckets: dict[str, list[TraceFieldOut]] = {role: [] for role, _ in roles}

        for key, value in exception_data.items():
            matched_role = None
            for role, _ in roles:
                suffix = f"_{role}"
                if key.endswith(suffix):
                    matched_role = role
                    key_display = key[: -len(suffix)]
                    break
            else:
                key_display = key
                owning_roles = [role for role, names in role_field_names.items() if key in names]
                if len(owning_roles) == 1:
                    matched_role = owning_roles[0]

            if matched_role is not None:
                role_buckets[matched_role].append(TraceFieldOut(field=_humanize_field_name(key_display), value=_format_value(value)))
                consumed_keys.add(key)

        for role, canonical_object in roles:
            objects.append(
                TraceObjectOut(
                    role=role,
                    canonical_object=canonical_object,
                    table_name=_physical_table_name(db, audit_test_id=audit_test.audit_test_id, canonical_object=canonical_object),
                    fields=role_buckets[role],
                )
            )

    other_fields = [
        TraceFieldOut(field=_humanize_field_name(k), value=_format_value(v))
        for k, v in exception_data.items()
        if k not in consumed_keys
    ]

    control_label = f"{control.control_code} — {control.control_name}" if control else (audit_test.test_name if audit_test else "This control")
    summary = (
        (_natural_summary(rule_definition, exception_data, record_id, control_label) if rule_definition else None)
        or (f"Record {record_id} did not pass the \"{control_label}\" check." if record_id else f"A record did not pass the \"{control_label}\" check.")
    )

    return ExceptionTraceOut(
        exception_id=exception.exception_id,
        control_code=control.control_code if control else None,
        control_name=control.control_name if control else None,
        execution_id=execution.execution_id if execution else exception.execution_id,
        executed_at=execution.started_at if execution else exception.detected_at,
        rule_type=rule_definition.get("rule_type") if rule_definition else None,
        summary=summary,
        objects=objects,
        other_fields=other_fields,
        severity=exception.severity,
        status=exception.status,
    )
