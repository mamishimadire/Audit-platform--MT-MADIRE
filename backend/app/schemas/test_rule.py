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

A third round, orthogonal to the rule shape itself:
  - ParameterReference as a FieldCondition/ThresholdRule value: a named,
    org-tunable number (e.g. "dormancy_days") instead of a fixed literal,
    so a client's own risk appetite can change 90 days to 60 without
    anyone editing or regenerating the rule. Resolved to a concrete number
    entirely on the platform side, before either rule engine ever sees the
    rule_definition — see app/services/rule_parameter_service.py — so
    rule_evaluation.py and gateway/gateway/rule_engine.py need no changes
    at all to support it.

A fourth round, closing two gaps found while re-deriving the remaining 47
untemplated controls against the CURRENT capability set rather than trusting
the prior pass's one-line category guesses (see migration 0059's docstring
for the full re-derivation):
  - MissingMatchRule.secondary_condition: filters secondary_object rows
    BEFORE the anti-join, mirroring primary_condition on the other side.
    Without it, "primary rows with no matching APPROVED secondary row"
    (e.g. AC-001: an active system_user with no *approved* access_request —
    a pending one must not count) could only be expressed as "no matching
    row AT ALL," silently treating an unapproved/pending match as if it
    satisfied the check. Also resolves the "audit_logs polymorphic
    entity_type/entity_id" gap (MD-005/DP-002/PY-010): entity_id only means
    "this specific record" once entity_type is filtered to the ONE entity
    type the primary object represents — previously an unfiltered anti-join
    against the whole shared audit_logs table would have matched entity_id
    values belonging to a completely different entity_type by coincidence.
  - DynamicRelativeDateComparison (on CrossMatchConditionRule) and its
    three-way counterpart ThreeWayDynamicRelativeDateComparison (on
    ThreeWayMatchRule): compares a date field to now() offset by a day
    count that is itself looked up from a field on the OTHER side of a
    join, per matched row — e.g. backup_jobs.run_at vs. now() minus
    backup_retention_rules.retention_days for that SAME system_id.
    Distinct from RelativeDate, which bakes ONE fixed day count into the
    template at authoring time (does not vary per row, per system, or per
    severity); distinct from FieldComparison/ThreeWayFieldComparison, which
    compare two raw field values with no "now() +/- N days" arithmetic at
    all. This is the genuine combination of both, and is what 0051/0054
    catalogued as the recurring "dynamic relative-date" gap blocking
    BK-004, OP-007, PM-001, VM-002, and DP-004 (each needs a per-row/
    per-severity/per-dataset day count sourced from data, not a fixed
    number that would silently go stale the moment a client edits their
    own retention/SLA table).
"""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class ParameterReference(BaseModel):
    """A named, org-tunable number substituted in at execution time instead
    of a fixed literal — e.g. key="dormancy_days" so a client's own risk
    appetite can change the number without editing or regenerating the
    rule. default is what the rule behaves as until an organization
    explicitly overrides that parameter (see rule_parameter_service.
    DEFAULT_PARAMETERS and get_parameters/set_parameters) — always stored
    and shown to an auditor as a plain positive magnitude ("180 days"),
    never a signed one. multiplier is applied AFTER lookup and is never
    org-editable — it's how a template embeds this reference inside a
    RelativeDate.relative_days and still gets the sign it needs (-1 for "N
    days in the past", +1 for "N days from now" or a plain threshold),
    without the org-facing number itself ever being negative."""

    kind: Literal["parameter"] = "parameter"
    key: str
    default: float
    multiplier: float = 1


class RelativeDate(BaseModel):
    """A point in time expressed relative to when the rule runs, not a
    fixed date — "90 days ago" stays "90 days ago" every time the test
    executes. relative_days is signed: -90 means 90 days in the past
    (typically compared with lt/lte — "this date is older than 90 days
    ago"), +30 means 30 days in the future (typically compared with
    lt/lte too — "this date is sooner than 30 days from now", e.g. a
    certificate expiring soon). May itself be a ParameterReference (with
    multiplier=-1/+1 matching the direction this rule needs) instead of a
    fixed int, so "how many days is too many" stays org-tunable even
    inside a relative-date comparison."""

    kind: Literal["relative_date"] = "relative_date"
    relative_days: int | ParameterReference


class FieldCondition(BaseModel):
    field: str  # canonical field, e.g. "employment_status"
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "is_null", "is_not_null", "matches", "in", "not_in"]
    # "matches" expects a regex pattern string; "in"/"not_in" expect a list.
    value: str | float | bool | RelativeDate | ParameterReference | list[str | float] | None = None


class ThresholdRule(BaseModel):
    rule_type: Literal["threshold"] = "threshold"
    object: str  # canonical object, e.g. "transaction"
    field: str
    operator: Literal["gt", "gte", "lt", "lte", "eq", "ne"]
    value: float | str | RelativeDate | ParameterReference

    def required_objects(self) -> set[str]:
        return {self.object}

    def required_fields_by_object(self) -> dict[str, set[str]]:
        return {self.object: {self.field}}


class DuplicateRule(BaseModel):
    """Flags every row whose group_by key combination isn't unique.

    When distinct_field is set, the check changes from "this exact
    group_by combination repeats" to "this group_by key has more than one
    DISTINCT value of distinct_field" — e.g. AC-008: group_by=["user_id"],
    distinct_field="ip_address" flags a login_history row when that
    account's own logins came from more than one distinct IP, a proxy for
    the account being used by more than one person. Plain row-count
    duplication (the default, distinct_field unset) would be the wrong
    check here — the same person logging in twice from the same IP is
    normal, not a finding."""

    rule_type: Literal["duplicate"] = "duplicate"
    object: str
    group_by: list[str] = Field(min_length=1)
    condition: FieldCondition | None = None  # filters rows before duplicate-detection
    distinct_field: str | None = None

    def required_objects(self) -> set[str]:
        return {self.object}

    def required_fields_by_object(self) -> dict[str, set[str]]:
        fields = set(self.group_by)
        if self.condition is not None:
            fields.add(self.condition.field)
        if self.distinct_field is not None:
            fields.add(self.distinct_field)
        return {self.object: fields}


class MissingMatchRule(BaseModel):
    """Rows in primary_object with no matching row in secondary_object on
    join_field (or, if the two sides don't share a canonical field name,
    join_field on the primary side and secondary_join_field on the
    secondary side). An optional primary_condition filters primary_object
    rows BEFORE the anti-join, so this can express "primary rows matching
    X that ALSO have no match in secondary" instead of only "all primary
    rows with no match." An optional secondary_condition, symmetrically,
    filters secondary_object rows BEFORE the anti-join — so "no match AT
    ALL" can become "no match that ALSO satisfies Y" (e.g. only an
    *approved* access_request counts; only an audit_logs row for the
    RIGHT entity_type counts), instead of treating any row that merely
    shares the join key, regardless of its own status, as satisfying the
    check."""

    rule_type: Literal["missing_match"] = "missing_match"
    primary_object: str
    secondary_object: str
    join_field: str  # canonical field name on primary_object
    secondary_join_field: str | None = None  # defaults to join_field when the two sides share a name
    primary_condition: FieldCondition | None = None
    secondary_condition: FieldCondition | None = None

    def _secondary_field(self) -> str:
        return self.secondary_join_field or self.join_field

    def required_objects(self) -> set[str]:
        return {self.primary_object, self.secondary_object}

    def required_fields_by_object(self) -> dict[str, set[str]]:
        primary_fields = {self.join_field}
        if self.primary_condition is not None:
            primary_fields.add(self.primary_condition.field)
        secondary_fields = {self._secondary_field()}
        if self.secondary_condition is not None:
            secondary_fields.add(self.secondary_condition.field)
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


class DynamicRelativeDateComparison(BaseModel):
    """Compares a date field on primary_object to now() offset by a day
    count that is itself read from a field on secondary_object of the SAME
    joined row — e.g. backup_jobs.run_at (primary_field) vs. now() minus
    backup_retention_rules.retention_days (secondary_field) for that same
    system_id, where retention_days varies per row instead of being one
    fixed number decided when the template was written. Distinct from
    RelativeDate (a single offset fixed at template-authoring time, the
    same for every row) and from FieldComparison (compares two raw values
    against each other, with no "now() +/- N days" arithmetic at all) —
    this is the genuine combination of both: a per-row day count used as
    date arithmetic against "now." direction is the sign applied to the
    looked-up day count before adding it to now(): -1 (the common case) is
    a retention/SLA-style "must not be older than N days" check; +1 would
    be an "expiring within N days, where N itself varies per row" check."""

    primary_field: str  # a date/datetime field on primary_object
    operator: Literal["lt", "lte", "gt", "gte"]
    secondary_field: str  # a numeric day-count field on secondary_object
    direction: Literal[-1, 1] = -1


class CrossMatchConditionRule(BaseModel):
    """
    Joined rows where BOTH per-side conditions hold — e.g.
    employee.employment_status == 'terminated' AND user.status == 'active',
    joined on employee_id — AND, if field_comparison is set, an additional
    condition comparing a field from each side against each other (not a
    literal) is also satisfied. If dynamic_relative_date_comparison is also
    set, an additional condition comparing a date field on one side to
    now() offset by a per-row day count read from the other side must also
    be satisfied — see DynamicRelativeDateComparison. field_comparison and
    dynamic_relative_date_comparison are independent and may both be set;
    when both are set, a joined row must satisfy both to be flagged.
    """

    rule_type: Literal["cross_match_condition"] = "cross_match_condition"
    primary_object: str
    secondary_object: str
    join_field: str  # canonical field name on primary_object
    secondary_join_field: str | None = None  # defaults to join_field when the two sides share a name
    condition_primary: FieldCondition
    condition_secondary: FieldCondition
    field_comparison: FieldComparison | None = None
    dynamic_relative_date_comparison: DynamicRelativeDateComparison | None = None

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
        if self.dynamic_relative_date_comparison is not None:
            primary_fields.add(self.dynamic_relative_date_comparison.primary_field)
            secondary_fields.add(self.dynamic_relative_date_comparison.secondary_field)
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


class ThreeWayDynamicRelativeDateComparison(BaseModel):
    """Like DynamicRelativeDateComparison, but for ThreeWayMatchRule, where
    the date field and the day-count field don't always live on "primary"
    vs "secondary" — e.g. PM-001: the release date lives on secondary
    (patch_releases.released_at), the SLA day count lives on tertiary
    (sla_rules.patch_within_days), reached only via a shared severity key
    one hop away from where the date itself lives. Same semantics as
    DynamicRelativeDateComparison otherwise: flags rows where operator
    compares the date field to now() + direction * the looked-up day
    count."""

    date_object: Literal["primary", "secondary", "tertiary"]
    date_field: str
    operator: Literal["lt", "lte", "gt", "gte"]
    offset_object: Literal["primary", "secondary", "tertiary"]
    offset_field: str
    direction: Literal[-1, 1] = -1


class ThreeWayMatchRule(BaseModel):
    """Joins three objects in a chain: primary <-> secondary on
    join_field_primary_secondary, then secondary <-> tertiary on
    join_field_secondary_tertiary — e.g. purchase_order <-> goods_receipt
    <-> supplier_invoice, the classic three-way match. Each side may have
    its own FieldCondition pre-filter (all optional — omit for "no filter,
    just join"). An optional field_comparison checks a field from any two
    of the three sides against each other post-join. An optional
    dynamic_relative_date_comparison, independent of field_comparison and
    usable alongside it, checks a date field from any side against now()
    offset by a per-row day count read from any (possibly different) side
    — see ThreeWayDynamicRelativeDateComparison.

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
    dynamic_relative_date_comparison: ThreeWayDynamicRelativeDateComparison | None = None

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
        if self.dynamic_relative_date_comparison is not None:
            add(obj_by_role[self.dynamic_relative_date_comparison.date_object], self.dynamic_relative_date_comparison.date_field)
            add(obj_by_role[self.dynamic_relative_date_comparison.offset_object], self.dynamic_relative_date_comparison.offset_field)
        return fields_by_obj


class FourWayFieldComparison(BaseModel):
    """Like ThreeWayFieldComparison, one role wider."""

    left_object: Literal["primary", "secondary", "tertiary", "quaternary"]
    left_field: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    right_object: Literal["primary", "secondary", "tertiary", "quaternary"]
    right_field: str


class FourWayDynamicRelativeDateComparison(BaseModel):
    """Like ThreeWayDynamicRelativeDateComparison, one role wider."""

    date_object: Literal["primary", "secondary", "tertiary", "quaternary"]
    date_field: str
    operator: Literal["lt", "lte", "gt", "gte"]
    offset_object: Literal["primary", "secondary", "tertiary", "quaternary"]
    offset_field: str
    direction: Literal[-1, 1] = -1


class FourWayMatchRule(BaseModel):
    """One hop past ThreeWayMatchRule: primary <-> secondary <-> tertiary
    <-> quaternary, chained the same way (each join's "other side" field
    name defaults to the same name, override when it differs). Exists for
    a dynamic per-row threshold lookup where the actor isn't on the
    transaction record itself but on a SEPARATE approval record one hop
    away — e.g. PR-002/PR-019/GL-007: transaction (primary) -> its own
    approval record (secondary, holds approved_by) -> that approver's role
    (tertiary, user_roles) -> the role's authorised limit (quaternary,
    approval_limits) -> field_comparison checks the transaction's amount
    against the limit. ThreeWayMatchRule's own built-in threshold-lookup
    shape (see its docstring) only reaches a limits table one hop from
    primary; this is for when it's two."""

    rule_type: Literal["four_way_match"] = "four_way_match"
    primary_object: str
    secondary_object: str
    tertiary_object: str
    quaternary_object: str
    join_field_primary_secondary: str
    secondary_join_field_1: str | None = None
    join_field_secondary_tertiary: str
    tertiary_join_field: str | None = None
    join_field_tertiary_quaternary: str
    quaternary_join_field: str | None = None
    condition_primary: FieldCondition | None = None
    condition_secondary: FieldCondition | None = None
    condition_tertiary: FieldCondition | None = None
    condition_quaternary: FieldCondition | None = None
    field_comparison: FourWayFieldComparison | None = None
    dynamic_relative_date_comparison: FourWayDynamicRelativeDateComparison | None = None

    def _secondary_ps_field(self) -> str:
        return self.secondary_join_field_1 or self.join_field_primary_secondary

    def _tertiary_field(self) -> str:
        return self.tertiary_join_field or self.join_field_secondary_tertiary

    def _quaternary_field(self) -> str:
        return self.quaternary_join_field or self.join_field_tertiary_quaternary

    def required_objects(self) -> set[str]:
        return {self.primary_object, self.secondary_object, self.tertiary_object, self.quaternary_object}

    def required_fields_by_object(self) -> dict[str, set[str]]:
        obj_by_role = {
            "primary": self.primary_object, "secondary": self.secondary_object,
            "tertiary": self.tertiary_object, "quaternary": self.quaternary_object,
        }
        fields_by_obj: dict[str, set[str]] = {}

        def add(obj: str, field: str) -> None:
            fields_by_obj.setdefault(obj, set()).add(field)

        add(self.primary_object, self.join_field_primary_secondary)
        add(self.secondary_object, self._secondary_ps_field())
        add(self.secondary_object, self.join_field_secondary_tertiary)
        add(self.tertiary_object, self._tertiary_field())
        add(self.tertiary_object, self.join_field_tertiary_quaternary)
        add(self.quaternary_object, self._quaternary_field())
        if self.condition_primary is not None:
            add(self.primary_object, self.condition_primary.field)
        if self.condition_secondary is not None:
            add(self.secondary_object, self.condition_secondary.field)
        if self.condition_tertiary is not None:
            add(self.tertiary_object, self.condition_tertiary.field)
        if self.condition_quaternary is not None:
            add(self.quaternary_object, self.condition_quaternary.field)
        if self.field_comparison is not None:
            add(obj_by_role[self.field_comparison.left_object], self.field_comparison.left_field)
            add(obj_by_role[self.field_comparison.right_object], self.field_comparison.right_field)
        if self.dynamic_relative_date_comparison is not None:
            add(obj_by_role[self.dynamic_relative_date_comparison.date_object], self.dynamic_relative_date_comparison.date_field)
            add(obj_by_role[self.dynamic_relative_date_comparison.offset_object], self.dynamic_relative_date_comparison.offset_field)
        return fields_by_obj


class BalanceRule(BaseModel):
    """Groups object's rows by group_by and flags every row in a group
    where SUM(debit_field) and SUM(credit_field) across the group don't
    match — the standard "does this journal balance" accounting check
    (GL-009: group_by=["journal_id"] on journal_lines). Distinct from
    DuplicateRule's distinct_field option, which counts distinct VALUES;
    this sums two NUMERIC fields per group and compares the totals.
    tolerance absorbs floating-point rounding, not a real business
    allowance — a group off by more than a cent or two is still a real
    imbalance."""

    rule_type: Literal["balance"] = "balance"
    object: str
    group_by: list[str] = Field(min_length=1)
    debit_field: str
    credit_field: str
    tolerance: float = 0.01

    def required_objects(self) -> set[str]:
        return {self.object}

    def required_fields_by_object(self) -> dict[str, set[str]]:
        return {self.object: set(self.group_by) | {self.debit_field, self.credit_field}}


TestRuleDefinition = Annotated[
    Union[ThresholdRule, DuplicateRule, MissingMatchRule, CrossMatchConditionRule, ThreeWayMatchRule, FourWayMatchRule, BalanceRule],
    Field(discriminator="rule_type"),
]


_RULE_CLASSES = {
    "threshold": ThresholdRule,
    "duplicate": DuplicateRule,
    "missing_match": MissingMatchRule,
    "cross_match_condition": CrossMatchConditionRule,
    "three_way_match": ThreeWayMatchRule,
    "four_way_match": FourWayMatchRule,
    "balance": BalanceRule,
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
