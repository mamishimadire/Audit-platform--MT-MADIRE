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

Four small, backward-compatible extensions (all new fields optional, so
every rule written before they existed still means exactly what it meant)
were added after the first 68 rule templates, once cataloguing the
remaining 89 untemplated controls showed the same few gaps recurring:
  - RelativeDate as a FieldCondition/ThresholdRule value: "field is older
    than N days" (e.g. a certificate expiring within 30 days, a review not
    done in the last 90) with no other way to express "compare a date field
    to now" existed before.
  - CrossMatchConditionRule.field_comparison: a condition evaluated AFTER
    the join, comparing one field on the primary side to one field on the
    secondary side (e.g. "flag joined rows where created_by == approved_by")
    — condition_primary/condition_secondary alone can only compare each
    side to a literal, never to each other.
  - MissingMatchRule.primary_condition: filters primary_object rows BEFORE
    checking for a match, so "unresolved critical findings with no linked
    remediation" doesn't have to anti-join the whole table.
  - MissingMatchRule.secondary_join_field / CrossMatchConditionRule's same
    field: the two sides of a join don't always share one canonical field
    name (e.g. device.asset_tag on one side, patch_deployments.device_
    asset_tag on the other) — defaults to join_field when omitted, so every
    existing rule is unaffected.
"""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class RelativeDate(BaseModel):
    """A point in time expressed relative to when the rule runs, not a
    fixed date — "90 days ago" stays "90 days ago" every time the test
    executes. relative_days is signed: -90 means 90 days in the past
    (typically compared with lt/lte — "this date is older than 90 days
    ago"), +30 means 30 days in the future (typically compared with
    lt/lte too — "this date is sooner than 30 days from now", e.g. a
    certificate expiring soon)."""

    kind: Literal["relative_date"] = "relative_date"
    relative_days: int


class FieldCondition(BaseModel):
    field: str  # canonical field, e.g. "employment_status"
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "is_null", "is_not_null"]
    value: str | float | bool | RelativeDate | None = None


class ThresholdRule(BaseModel):
    rule_type: Literal["threshold"] = "threshold"
    object: str  # canonical object, e.g. "transaction"
    field: str
    operator: Literal["gt", "gte", "lt", "lte", "eq", "ne"]
    value: float | str | RelativeDate

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
    """Rows in primary_object with no matching row in secondary_object on
    join_field (or, if the two sides don't share a canonical field name,
    join_field on the primary side and secondary_join_field on the
    secondary side). An optional primary_condition filters primary_object
    rows BEFORE the anti-join, so this can express "primary rows matching
    X that ALSO have no match in secondary" instead of only "all primary
    rows with no match.\""""

    rule_type: Literal["missing_match"] = "missing_match"
    primary_object: str
    secondary_object: str
    join_field: str  # canonical field name on primary_object
    secondary_join_field: str | None = None  # defaults to join_field when the two sides share a name
    primary_condition: FieldCondition | None = None

    def _secondary_field(self) -> str:
        return self.secondary_join_field or self.join_field

    def required_objects(self) -> set[str]:
        return {self.primary_object, self.secondary_object}

    def required_fields_by_object(self) -> dict[str, set[str]]:
        primary_fields = {self.join_field}
        if self.primary_condition is not None:
            primary_fields.add(self.primary_condition.field)
        secondary_fields = {self._secondary_field()}
        # A dict can't hold two entries under the same key — when both
        # sides are the same object (a self-join), the fields must be
        # merged into one entry, or whichever assignment runs second would
        # silently overwrite the first and drop half the real requirement.
        if self.primary_object == self.secondary_object:
            return {self.primary_object: primary_fields | secondary_fields}
        return {self.primary_object: primary_fields, self.secondary_object: secondary_fields}


class FieldComparison(BaseModel):
    """A condition evaluated after primary_object and secondary_object have
    already been joined, comparing a field on one side to a field on the
    other — e.g. primary_field="created_by", operator="eq",
    secondary_field="approved_by" flags joined rows where the same person
    both created and approved a record. Distinct from FieldCondition, which
    only ever compares a field to a fixed literal."""

    primary_field: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    secondary_field: str


class CrossMatchConditionRule(BaseModel):
    """
    Joined rows where BOTH per-side conditions hold — e.g.
    employee.employment_status == 'terminated' AND user.status == 'active',
    joined on employee_id — AND, if field_comparison is set, an additional
    condition comparing a field from each side against each other (not a
    literal) is also satisfied.
    """

    rule_type: Literal["cross_match_condition"] = "cross_match_condition"
    primary_object: str
    secondary_object: str
    join_field: str  # canonical field name on primary_object
    secondary_join_field: str | None = None  # defaults to join_field when the two sides share a name
    condition_primary: FieldCondition
    condition_secondary: FieldCondition
    field_comparison: FieldComparison | None = None

    def _secondary_field(self) -> str:
        return self.secondary_join_field or self.join_field

    def required_objects(self) -> set[str]:
        return {self.primary_object, self.secondary_object}

    def required_fields_by_object(self) -> dict[str, set[str]]:
        primary_fields = {self.join_field, self.condition_primary.field}
        secondary_fields = {self._secondary_field(), self.condition_secondary.field}
        if self.field_comparison is not None:
            primary_fields.add(self.field_comparison.primary_field)
            secondary_fields.add(self.field_comparison.secondary_field)
        # Same dict-key-collision hazard as MissingMatchRule above — a
        # self-join (primary_object == secondary_object, e.g. OP-006's
        # "critical AND unresolved" pattern, or field_comparison's SOD
        # creator/approver check on one record) must merge into a single
        # entry, not let the second assignment silently drop the first.
        if self.primary_object == self.secondary_object:
            return {self.primary_object: primary_fields | secondary_fields}
        return {self.primary_object: primary_fields, self.secondary_object: secondary_fields}


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
