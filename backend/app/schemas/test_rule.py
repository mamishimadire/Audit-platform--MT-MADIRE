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

A second round of additions, for the categories of control that remained
blocked after the first round — again all-optional/backward-compatible:
  - FieldCondition operators "matches" (regex, e.g. a generic-account
    username pattern), "in" and "not_in" (list membership, e.g. a fixed
    set of approved remote-access tools).
  - DuplicateRule.condition: filters rows BEFORE duplicate-detection, same
    idea as MissingMatchRule.primary_condition — "repeated attempts to
    reach a *blocked* site" needs the blocked ones singled out first, not
    duplicates across all traffic.
  - ThreeWayMatchRule: joins three objects in a chain (primary-secondary,
    secondary-tertiary), each side optionally filtered, with an optional
    post-join field_comparison between any two sides. Covers genuine
    three-way matches (PO/GRN/invoice) AND dynamic per-row threshold
    lookups (a payment's amount vs. an approval_limits row for that
    specific approver's role) — the latter is just a three-way join where
    the third object IS the lookup table, compared via field_comparison
    instead of a fixed literal.
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
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "is_null", "is_not_null", "matches", "in", "not_in"]
    # "matches" expects a regex pattern string; "in"/"not_in" expect a list.
    value: str | float | bool | RelativeDate | list[str | float] | None = None


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
    condition: FieldCondition | None = None  # filters rows before duplicate-detection

    def required_objects(self) -> set[str]:
        return {self.object}

    def required_fields_by_object(self) -> dict[str, set[str]]:
        fields = set(self.group_by)
        if self.condition is not None:
            fields.add(self.condition.field)
        return {self.object: fields}


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


class ThreeWayFieldComparison(BaseModel):
    """Like FieldComparison, but for ThreeWayMatchRule where the two sides
    being compared aren't always "primary vs secondary" — e.g. a goods-
    receipt quantity (secondary) vs. an invoice quantity (tertiary)."""

    left_object: Literal["primary", "secondary", "tertiary"]
    left_field: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    right_object: Literal["primary", "secondary", "tertiary"]
    right_field: str


class ThreeWayMatchRule(BaseModel):
    """Joins three objects in a chain: primary <-> secondary on
    join_field_primary_secondary, then secondary <-> tertiary on
    join_field_secondary_tertiary — e.g. purchase_order <-> goods_receipt
    <-> supplier_invoice, the classic three-way match. Each side may have
    its own FieldCondition pre-filter (all optional — omit for "no filter,
    just join"). An optional field_comparison checks a field from any two
    of the three sides against each other post-join.

    Also how a dynamic per-row threshold lookup is expressed (e.g. "does
    this payment exceed the approval limit for THIS SPECIFIC approver's
    role"): the "tertiary" object is the lookup/limits table, joined in by
    whatever key ties a row to its limit, then field_comparison checks the
    record's value against the limit's value — a fixed literal threshold
    never has to be hard-coded into the rule itself.
    """

    rule_type: Literal["three_way_match"] = "three_way_match"
    primary_object: str
    secondary_object: str
    tertiary_object: str
    join_field_primary_secondary: str  # canonical field name on primary_object
    secondary_join_field_1: str | None = None  # name on secondary_object for the primary<->secondary join; defaults to join_field_primary_secondary
    join_field_secondary_tertiary: str  # canonical field name on secondary_object
    tertiary_join_field: str | None = None  # name on tertiary_object for the secondary<->tertiary join; defaults to join_field_secondary_tertiary
    condition_primary: FieldCondition | None = None
    condition_secondary: FieldCondition | None = None
    condition_tertiary: FieldCondition | None = None
    field_comparison: ThreeWayFieldComparison | None = None

    def _secondary_ps_field(self) -> str:
        return self.secondary_join_field_1 or self.join_field_primary_secondary

    def _tertiary_field(self) -> str:
        return self.tertiary_join_field or self.join_field_secondary_tertiary

    def required_objects(self) -> set[str]:
        return {self.primary_object, self.secondary_object, self.tertiary_object}

    def required_fields_by_object(self) -> dict[str, set[str]]:
        obj_by_role = {"primary": self.primary_object, "secondary": self.secondary_object, "tertiary": self.tertiary_object}
        fields_by_obj: dict[str, set[str]] = {}

        def add(obj: str, field: str) -> None:
            fields_by_obj.setdefault(obj, set()).add(field)

        add(self.primary_object, self.join_field_primary_secondary)
        add(self.secondary_object, self._secondary_ps_field())
        add(self.secondary_object, self.join_field_secondary_tertiary)
        add(self.tertiary_object, self._tertiary_field())
        if self.condition_primary is not None:
            add(self.primary_object, self.condition_primary.field)
        if self.condition_secondary is not None:
            add(self.secondary_object, self.condition_secondary.field)
        if self.condition_tertiary is not None:
            add(self.tertiary_object, self.condition_tertiary.field)
        if self.field_comparison is not None:
            add(obj_by_role[self.field_comparison.left_object], self.field_comparison.left_field)
            add(obj_by_role[self.field_comparison.right_object], self.field_comparison.right_field)
        return fields_by_obj


TestRuleDefinition = Annotated[
    Union[ThresholdRule, DuplicateRule, MissingMatchRule, CrossMatchConditionRule, ThreeWayMatchRule],
    Field(discriminator="rule_type"),
]


_RULE_CLASSES = {
    "threshold": ThresholdRule,
    "duplicate": DuplicateRule,
    "missing_match": MissingMatchRule,
    "cross_match_condition": CrossMatchConditionRule,
    "three_way_match": ThreeWayMatchRule,
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
