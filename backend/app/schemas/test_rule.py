"""
Rule definitions for the Audit Test Engine (product spec Section 17).

Four composable primitives cover the five worked examples in the spec:
  - threshold           -> "transactions > configured amount"
  - duplicate            -> "duplicate invoice number"
  - missing_match        -> "payments without corresponding invoices"
  - cross_match_condition -> "terminated employee with active access" (the
                             AC-002 flagship example), and covers "date
                             anomaly" style checks too via a date field +
                             operator on a single object (primary==secondary
                             not required — see note on CrossMatchConditionRule)

Deliberately NOT a general-purpose expression language or arbitrary
SQL/Python — this is data executed against a client's live database by a
Gateway running on their machine, so the shape of what can be expressed is
the actual security boundary. Every field/object name here is a *canonical*
name (Section 19); the Gateway resolves canonical -> physical via the
audit test's approved test_data_mappings before running anything.
"""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class FieldCondition(BaseModel):
    field: str  # canonical field, e.g. "employment_status"
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "is_null", "is_not_null"]
    value: str | float | bool | None = None


class ThresholdRule(BaseModel):
    rule_type: Literal["threshold"] = "threshold"
    object: str  # canonical object, e.g. "transaction"
    field: str
    operator: Literal["gt", "gte", "lt", "lte", "eq", "ne"]
    value: float | str

    def required_objects(self) -> set[str]:
        return {self.object}

    def required_fields_by_object(self) -> dict[str, set[str]]:
        return {self.object: {self.field}}


class DuplicateRule(BaseModel):
    rule_type: Literal["duplicate"] = "duplicate"
    object: str
    group_by: list[str] = Field(min_length=1)

    def required_objects(self) -> set[str]:
        return {self.object}

    def required_fields_by_object(self) -> dict[str, set[str]]:
        return {self.object: set(self.group_by)}


class MissingMatchRule(BaseModel):
    """Rows in primary_object with no matching row in secondary_object on join_field."""

    rule_type: Literal["missing_match"] = "missing_match"
    primary_object: str
    secondary_object: str
    join_field: str  # canonical field name, present on both objects

    def required_objects(self) -> set[str]:
        return {self.primary_object, self.secondary_object}

    def required_fields_by_object(self) -> dict[str, set[str]]:
        return {self.primary_object: {self.join_field}, self.secondary_object: {self.join_field}}


class CrossMatchConditionRule(BaseModel):
    """
    Joined rows where BOTH conditions hold — e.g. employee.employment_status
    == 'terminated' AND user.status == 'active', joined on employee_id.
    """

    rule_type: Literal["cross_match_condition"] = "cross_match_condition"
    primary_object: str
    secondary_object: str
    join_field: str
    condition_primary: FieldCondition
    condition_secondary: FieldCondition

    def required_objects(self) -> set[str]:
        return {self.primary_object, self.secondary_object}

    def required_fields_by_object(self) -> dict[str, set[str]]:
        return {
            self.primary_object: {self.join_field, self.condition_primary.field},
            self.secondary_object: {self.join_field, self.condition_secondary.field},
        }


TestRuleDefinition = Annotated[
    Union[ThresholdRule, DuplicateRule, MissingMatchRule, CrossMatchConditionRule],
    Field(discriminator="rule_type"),
]


_RULE_CLASSES = {
    "threshold": ThresholdRule,
    "duplicate": DuplicateRule,
    "missing_match": MissingMatchRule,
    "cross_match_condition": CrossMatchConditionRule,
}


def required_objects_for(rule_definition: dict) -> set[str]:
    cls = _RULE_CLASSES.get(rule_definition.get("rule_type"))
    if cls is None:
        return set()
    return cls.model_validate(rule_definition).required_objects()


def required_fields_by_object_for(rule_definition: dict) -> dict[str, set[str]]:
    """Which specific canonical fields the rule actually reads, per required
    object — the fields that matter for testing, distinct from every other
    column a discovered table happens to have."""
    cls = _RULE_CLASSES.get(rule_definition.get("rule_type"))
    if cls is None:
        return {}
    return cls.model_validate(rule_definition).required_fields_by_object()
