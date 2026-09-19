"""
"Show the auditor exactly what will be tested before they activate a
control" — a plain-English, SOURCE/JOIN/FILTER/TEST/RESULT breakdown of a
rule_definition, built the same way for a not-yet-generated control
template as for an already-active TestRule, so it can be shown at every
stage: previewing a template before "Generate from control template",
reviewing a pending rule before approving it, or just understanding an
active one. Never executes anything — this is purely descriptive, reading
already-approved test_data_mappings to show which physical column each
canonical field actually resolves to (or "not mapped yet" if it doesn't).
"""
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit_test import AuditTest, TestDataMapping
from app.models.data_source import DataEntity, DataField
from app.schemas.test_rule import required_fields_by_object_for
from app.services.mapping_service import list_mappings
from app.services.rule_parameter_service import get_parameters

_OPERATOR_WORDS = {
    "eq": "is",
    "ne": "is not",
    "gt": "is greater than",
    "gte": "is at least",
    "lt": "is less than",
    "lte": "is at most",
    "is_null": "is empty",
    "is_not_null": "is not empty",
    "matches": "matches the pattern",
    "in": "is one of",
    "not_in": "is none of",
}


def _describe_parameter(value: dict, parameters: dict[str, float]) -> tuple[float, str]:
    """Returns (resolved_number, plain-English note) for a ParameterReference
    — resolved_number already has multiplier applied (so a caller embedding
    this inside a RelativeDate gets the correctly-signed day count), the
    note names the setting and whether this org has customized it."""
    key = value["key"]
    magnitude = parameters.get(key, value.get("default"))
    origin = "this org's default" if magnitude == value.get("default") else "customized by this org"
    note = f"an adjustable setting called '{key}' — currently {origin}, changeable without touching this rule"
    return magnitude * value.get("multiplier", 1), note


def _describe_value(value: Any, parameters: dict[str, float]) -> str:
    if isinstance(value, dict) and value.get("kind") == "relative_date":
        days = value["relative_days"]
        if isinstance(days, dict) and days.get("kind") == "parameter":
            resolved_days, note = _describe_parameter(days, parameters)
            return f"{abs(resolved_days):g} days {'from now' if resolved_days >= 0 else 'ago'} ({note})"
        return f"{abs(days)} days {'from now' if days >= 0 else 'ago'} (recalculated every run)"
    if isinstance(value, dict) and value.get("kind") == "parameter":
        resolved, note = _describe_parameter(value, parameters)
        return f"{resolved:g} ({note})"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _describe_condition(object_label: str, condition: dict, parameters: dict[str, float]) -> str:
    op = _OPERATOR_WORDS.get(condition["operator"], condition["operator"])
    field = f"{object_label}.{condition['field']}"
    if condition["operator"] in ("is_null", "is_not_null"):
        return f"{field} {op}"
    return f"{field} {op} {_describe_value(condition.get('value'), parameters)}"


def _describe_dynamic_relative_date(
    date_label: str, date_field: str, operator: str, offset_label: str, offset_field: str, direction: int
) -> str:
    """Plain-English rendering of a DynamicRelativeDateComparison/
    ThreeWayDynamicRelativeDateComparison — the day-count offset is read
    from a field on another row at execution time, not a fixed number, so
    it's described by naming that field rather than by a resolved value
    the way _describe_value resolves a ParameterReference."""
    op = _OPERATOR_WORDS.get(operator, operator)
    sign = "minus" if direction == -1 else "plus"
    return f"{date_label}.{date_field} {op} (now {sign} {offset_label}.{offset_field} days)"


def _mapped_field_label(mappings_by_canonical: dict[str, tuple[DataEntity | None, DataField | None]], canonical_object: str, field: str) -> str:
    entity, data_field = mappings_by_canonical.get(f"{canonical_object}.{field}", (None, None))
    if entity is None or data_field is None:
        return "not mapped yet"
    return f"{entity.entity_name}.{data_field.field_name}"


def build_rule_preview(db: Session, *, audit_test_id: uuid.UUID, rule_definition: dict) -> dict:
    audit_test = db.get(AuditTest, audit_test_id)
    parameters = get_parameters(db, organization_id=audit_test.organization_id) if audit_test else {}

    mappings = list_mappings(db, audit_test_id=audit_test_id)
    mappings_by_canonical: dict[str, tuple[DataEntity | None, DataField | None]] = {}
    for m in mappings:
        if m.mapping_status != "approved" or not m.canonical_field:
            continue
        entity = db.get(DataEntity, m.entity_id) if m.entity_id else None
        field = db.get(DataField, m.field_id) if m.field_id else None
        mappings_by_canonical[m.canonical_field] = (entity, field)

    def physical(obj: str, field: str) -> str:
        return _mapped_field_label(mappings_by_canonical, obj, field)

    rule_type = rule_definition.get("rule_type")
    required = required_fields_by_object_for(rule_definition)
    field_mappings = [
        {"canonical": f"{obj}.{field}", "physical": physical(obj, field), "mapped": f"{obj}.{field}" in mappings_by_canonical}
        for obj, fields in required.items()
        for field in sorted(fields)
    ]

    if rule_type == "threshold":
        obj = rule_definition["object"]
        source = obj
        test_condition = _describe_condition(obj, {"field": rule_definition["field"], "operator": rule_definition["operator"], "value": rule_definition["value"]}, parameters)
        joins: list[str] = []
        filters: list[str] = []
        pass_condition = f"No {obj} record has {test_condition[len(obj) + 1:]}"

    elif rule_type == "duplicate":
        obj = rule_definition["object"]
        source = obj
        joins = []
        group_by = ", ".join(f"{obj}.{f}" for f in rule_definition["group_by"])
        filters = [_describe_condition(obj, rule_definition["condition"], parameters)] if rule_definition.get("condition") else []
        distinct_field = rule_definition.get("distinct_field")
        if distinct_field:
            test_condition = f"{group_by} has more than one distinct {obj}.{distinct_field} value"
            pass_condition = f"Every {group_by} has a single {obj}.{distinct_field} value"
        else:
            test_condition = f"More than one {obj} record shares the same {group_by}"
            pass_condition = f"Every {group_by} combination is unique"

    elif rule_type == "missing_match":
        primary, secondary = rule_definition["primary_object"], rule_definition["secondary_object"]
        join_field = rule_definition["join_field"]
        secondary_field = rule_definition.get("secondary_join_field") or join_field
        source = primary
        bridge = rule_definition.get("bridge_object")
        if bridge:
            joins = [
                f"{primary}.{join_field} = {bridge}.{rule_definition['bridge_join_field']}",
                f"{bridge}.{rule_definition['bridge_secondary_join_field']} = {secondary}.{secondary_field}",
            ]
        else:
            joins = [f"{primary}.{join_field} = {secondary}.{secondary_field}"]
        filters = [_describe_condition(primary, rule_definition["primary_condition"], parameters)] if rule_definition.get("primary_condition") else []
        if bridge and rule_definition.get("bridge_condition"):
            filters.append(_describe_condition(bridge, rule_definition["bridge_condition"], parameters))
        if rule_definition.get("gate_object"):
            gate_obj = rule_definition["gate_object"]
            gate_field = rule_definition["gate_join_field"]
            gate_secondary_field = rule_definition.get("gate_secondary_join_field") or gate_field
            joins.append(f"{primary}.{gate_field} = {gate_obj}.{gate_secondary_field}")
            if rule_definition.get("gate_condition"):
                filters.append(_describe_condition(gate_obj, rule_definition["gate_condition"], parameters))
        via = f" (through {bridge})" if bridge else ""
        if rule_definition.get("secondary_condition"):
            filters.append(_describe_condition(secondary, rule_definition["secondary_condition"], parameters))
            test_condition = f"A {primary} record has no matching {secondary} record{via} where " + _describe_condition(
                secondary, rule_definition["secondary_condition"], parameters
            )
            pass_condition = f"Every {primary} record has a matching {secondary} record{via} where " + _describe_condition(
                secondary, rule_definition["secondary_condition"], parameters
            )
        else:
            test_condition = f"A {primary} record has no matching {secondary} record{via}"
            pass_condition = f"Every {primary} record has a matching {secondary} record{via}"

    elif rule_type == "cross_match_condition":
        primary, secondary = rule_definition["primary_object"], rule_definition["secondary_object"]
        join_field = rule_definition["join_field"]
        secondary_field = rule_definition.get("secondary_join_field") or join_field
        source = primary
        joins = [f"{primary}.{join_field} = {secondary}.{secondary_field}"]
        filters = [
            _describe_condition(primary, rule_definition["condition_primary"], parameters),
            _describe_condition(secondary, rule_definition["condition_secondary"], parameters),
        ]
        test_condition = " AND ".join(filters)
        if rule_definition.get("field_comparison"):
            fc = rule_definition["field_comparison"]
            comparison = f"{primary}.{fc['primary_field']} {_OPERATOR_WORDS.get(fc['operator'], fc['operator'])} {secondary}.{fc['secondary_field']}"
            filters.append(comparison)
            test_condition += f" AND {comparison}"
        if rule_definition.get("dynamic_relative_date_comparison"):
            dc = rule_definition["dynamic_relative_date_comparison"]
            comparison = _describe_dynamic_relative_date(
                primary, dc["primary_field"], dc["operator"], secondary, dc["secondary_field"], dc.get("direction", -1)
            )
            filters.append(comparison)
            test_condition += f" AND {comparison}"
        pass_condition = "No joined record satisfies all of the above at once"

    elif rule_type == "three_way_match":
        primary, secondary, tertiary = rule_definition["primary_object"], rule_definition["secondary_object"], rule_definition["tertiary_object"]
        jf1 = rule_definition["join_field_primary_secondary"]
        jf1_secondary = rule_definition.get("secondary_join_field_1") or jf1
        jf2 = rule_definition["join_field_secondary_tertiary"]
        jf2_tertiary = rule_definition.get("tertiary_join_field") or jf2
        source = primary
        joins = [f"{primary}.{jf1} = {secondary}.{jf1_secondary}", f"{secondary}.{jf2} = {tertiary}.{jf2_tertiary}"]
        filters = []
        for role, obj in (("condition_primary", primary), ("condition_secondary", secondary), ("condition_tertiary", tertiary)):
            if rule_definition.get(role):
                filters.append(_describe_condition(obj, rule_definition[role], parameters))
        test_condition = " AND ".join(filters) if filters else f"{primary}, {secondary}, and {tertiary} records are linked together"
        if rule_definition.get("field_comparison"):
            fc = rule_definition["field_comparison"]
            role_obj = {"primary": primary, "secondary": secondary, "tertiary": tertiary}
            comparison = f"{role_obj[fc['left_object']]}.{fc['left_field']} {_OPERATOR_WORDS.get(fc['operator'], fc['operator'])} {role_obj[fc['right_object']]}.{fc['right_field']}"
            filters.append(comparison)
            test_condition = f"{test_condition} AND {comparison}" if test_condition else comparison
        if rule_definition.get("dynamic_relative_date_comparison"):
            dc = rule_definition["dynamic_relative_date_comparison"]
            role_obj = {"primary": primary, "secondary": secondary, "tertiary": tertiary}
            comparison = _describe_dynamic_relative_date(
                role_obj[dc["date_object"]], dc["date_field"], dc["operator"], role_obj[dc["offset_object"]], dc["offset_field"], dc.get("direction", -1)
            )
            filters.append(comparison)
            test_condition = f"{test_condition} AND {comparison}" if test_condition else comparison
        pass_condition = "No linked set of records satisfies all of the above at once"

    elif rule_type == "four_way_match":
        primary, secondary, tertiary, quaternary = (
            rule_definition["primary_object"], rule_definition["secondary_object"],
            rule_definition["tertiary_object"], rule_definition["quaternary_object"],
        )
        jf1 = rule_definition["join_field_primary_secondary"]
        jf1_secondary = rule_definition.get("secondary_join_field_1") or jf1
        jf2 = rule_definition["join_field_secondary_tertiary"]
        jf2_tertiary = rule_definition.get("tertiary_join_field") or jf2
        jf3 = rule_definition["join_field_tertiary_quaternary"]
        jf3_quaternary = rule_definition.get("quaternary_join_field") or jf3
        source = primary
        joins = [
            f"{primary}.{jf1} = {secondary}.{jf1_secondary}",
            f"{secondary}.{jf2} = {tertiary}.{jf2_tertiary}",
            f"{tertiary}.{jf3} = {quaternary}.{jf3_quaternary}",
        ]
        filters = []
        for role, obj in (
            ("condition_primary", primary), ("condition_secondary", secondary),
            ("condition_tertiary", tertiary), ("condition_quaternary", quaternary),
        ):
            if rule_definition.get(role):
                filters.append(_describe_condition(obj, rule_definition[role], parameters))
        test_condition = (
            " AND ".join(filters) if filters else f"{primary}, {secondary}, {tertiary}, and {quaternary} records are linked together"
        )
        role_obj = {"primary": primary, "secondary": secondary, "tertiary": tertiary, "quaternary": quaternary}
        if rule_definition.get("field_comparison"):
            fc = rule_definition["field_comparison"]
            comparison = f"{role_obj[fc['left_object']]}.{fc['left_field']} {_OPERATOR_WORDS.get(fc['operator'], fc['operator'])} {role_obj[fc['right_object']]}.{fc['right_field']}"
            filters.append(comparison)
            test_condition = f"{test_condition} AND {comparison}" if test_condition else comparison
        if rule_definition.get("dynamic_relative_date_comparison"):
            dc = rule_definition["dynamic_relative_date_comparison"]
            comparison = _describe_dynamic_relative_date(
                role_obj[dc["date_object"]], dc["date_field"], dc["operator"], role_obj[dc["offset_object"]], dc["offset_field"], dc.get("direction", -1)
            )
            filters.append(comparison)
            test_condition = f"{test_condition} AND {comparison}" if test_condition else comparison
        pass_condition = "No linked set of records satisfies all of the above at once"

    elif rule_type == "balance":
        obj = rule_definition["object"]
        source = obj
        joins = []
        filters = []
        group_by = ", ".join(f"{obj}.{f}" for f in rule_definition["group_by"])
        debit_field, credit_field = rule_definition["debit_field"], rule_definition["credit_field"]
        test_condition = f"For each {group_by}, SUM({obj}.{debit_field}) does not equal SUM({obj}.{credit_field})"
        pass_condition = f"Every {group_by} has matching debit and credit totals"

    elif rule_type == "baseline_comparison":
        obj, baseline_obj = rule_definition["object"], rule_definition["baseline_object"]
        source = obj
        joins = []
        filters = [_describe_condition(obj, rule_definition["condition"], parameters)] if rule_definition.get("condition") else []
        baseline_desc = f"{baseline_obj}.{rule_definition['baseline_value_field']}"
        if rule_definition.get("baseline_key_field"):
            baseline_desc += f" where {baseline_obj}.{rule_definition['baseline_key_field']} = {rule_definition['baseline_key_value']}"
        comparison = f"{obj}.{rule_definition['field']} {_OPERATOR_WORDS.get(rule_definition['operator'], rule_definition['operator'])} {baseline_desc}"
        test_condition = comparison if not filters else f"{' AND '.join(filters)} AND {comparison}"
        pass_condition = f"No {obj} record violates the {baseline_obj} baseline"

    elif rule_type == "reconciliation":
        ledger_obj, subledger_obj = rule_definition["ledger_object"], rule_definition["subledger_object"]
        source = ledger_obj
        joins = [f"{ledger_obj}.{rule_definition['ledger_key_field']} = SUM({subledger_obj}.{rule_definition['subledger_key_field']})"]
        filters = []
        test_condition = (
            f"{ledger_obj}.{rule_definition['ledger_value_field']} does not equal "
            f"SUM({subledger_obj}.{rule_definition['subledger_value_field']}) for the same {rule_definition['ledger_key_field']}"
        )
        pass_condition = f"Every {ledger_obj} record reconciles to its {subledger_obj} total"

    elif rule_type == "conflict_matrix":
        role_perm_obj, rules_obj = rule_definition["role_permission_object"], rule_definition["rules_object"]
        source = role_perm_obj
        joins = [f"{role_perm_obj}.{rule_definition['role_field']} matched against every pair in {rules_obj}.{rule_definition['conflict_field']}"]
        filters = []
        test_condition = f"A {role_perm_obj}.{rule_definition['role_field']} holds BOTH permissions of some {rules_obj} conflict pair"
        pass_condition = f"No role holds both permissions of any {rules_obj} conflict pair"

    else:
        source = "—"
        joins = []
        filters = []
        test_condition = f"Unrecognized rule type: {rule_type}"
        pass_condition = "—"

    return {
        "rule_type": rule_type,
        "source": source,
        "joins": joins,
        "filters": filters,
        "test_condition": test_condition,
        "pass_condition": pass_condition,
        "field_mappings": field_mappings,
    }
