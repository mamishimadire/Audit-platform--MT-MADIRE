import uuid
from datetime import datetime
from typing import Literal

from app.schemas.common import OrmModel


class MappingSuggestion(OrmModel):
    field_id: uuid.UUID
    field_name: str
    data_type: str | None
    suggested_canonical_field: str
    confidence_score: float
    # What the column actually holds vs what the canonical field needs — see
    # app.core.value_profile. Only set when it is a contradiction (the
    # suggestion was demoted for it); None means no objection, not "verified".
    value_fit_score: float | None = None
    value_fit_reason: str | None = None
    # Set when this column is a join key whose relationship the data does not
    # support (or that has more than one plausible pairing) - see join_resolution.
    relationship_reason: str | None = None


class TestDataMappingCreate(OrmModel):
    data_source_id: uuid.UUID
    entity_id: uuid.UUID
    field_id: uuid.UUID | None = None
    canonical_field: str
    confidence_score: float | None = None


class TestDataMappingUpdate(OrmModel):
    canonical_field: str


class MappingRejectRequest(OrmModel):
    reason: str


class TestDataMappingOut(OrmModel):
    mapping_id: uuid.UUID
    audit_test_id: uuid.UUID
    data_source_id: uuid.UUID
    entity_id: uuid.UUID
    field_id: uuid.UUID | None
    canonical_field: str | None
    confidence_score: float | None
    mapping_status: str
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    created_by: uuid.UUID | None
    rejected_by: uuid.UUID | None
    rejected_at: datetime | None
    rejected_reason: str | None
    version: int
    supersedes_mapping_id: uuid.UUID | None


class RequiredFieldStatus(OrmModel):
    """One field the rule actually reads — 'significant' in the sense that
    testing cannot run without it, unlike every other column a discovered
    table happens to have."""

    canonical_field: str
    mapped: bool
    mapping_id: uuid.UUID | None = None
    field_name: str | None = None
    mapping_status: str | None = None
    # A field the rule joins on: it identifies rows rather than describing them.
    is_join_key: bool = False


class RequiredObjectStatus(OrmModel):
    canonical_object: str
    entity_id: uuid.UUID | None = None
    entity_name: str | None = None
    data_source_id: uuid.UUID | None = None
    source_name: str | None = None
    required_fields: list[RequiredFieldStatus]
    fully_mapped: bool


class MappingReadinessOut(OrmModel):
    has_rule: bool
    ready: bool
    objects: list[RequiredObjectStatus]


class RulePreviewFieldMapping(OrmModel):
    canonical: str
    physical: str
    mapped: bool


class RulePreviewOut(OrmModel):
    """A plain-English SOURCE/JOIN/FILTER/TEST/PASS breakdown of a rule —
    shown before a control is even activated, so an auditor can see
    exactly what will be tested rather than trusting the automated result
    blindly."""

    rule_type: str
    source: str
    joins: list[str]
    filters: list[str]
    test_condition: str
    pass_condition: str
    field_mappings: list[RulePreviewFieldMapping]


RelationshipCheckStatus = Literal["validated", "weak", "not_available"]


class RelationshipCheckOut(OrmModel):
    """A field mapped correctly by NAME doesn't guarantee the two sides
    actually share the same values — this queries each side's live data
    (direct connections only; a Gateway-based one can't be queried on
    demand) and reports how much the two value sets genuinely overlap.
    Advisory, not a hard gate: real client data is often legitimately
    messy (soft-deleted rows, partial exports), so a low match rate is
    surfaced prominently rather than blocking activation outright."""

    primary_object: str
    secondary_object: str
    join_field: str
    secondary_join_field: str  # equal to join_field unless the rule's two sides use different canonical field names
    primary_sample_count: int
    secondary_sample_count: int
    overlap_count: int
    match_rate: float
    status: RelationshipCheckStatus
    detail: str


JoinVerdict = Literal["valid", "ambiguous", "contradicted", "unverified", "unresolved"]


class JoinColumnOut(OrmModel):
    field_id: uuid.UUID | None = None
    table: str
    column: str


class JoinAlternativeOut(OrmModel):
    left: JoinColumnOut
    right: JoinColumnOut
    score: float


class JoinResolutionOut(OrmModel):
    """One join a control's rule needs (e.g. api_access.user_id <-> user.user_id),
    resolved against the client's actual tables: which columns satisfy it, and
    how much the schema and the data back that up."""

    primitive: str
    requires_left: str
    requires_right: str
    verdict: JoinVerdict
    reason: str
    left: JoinColumnOut | None = None
    right: JoinColumnOut | None = None
    relationship: Literal["declared_fk", "inferred", "none"] = "none"
    relationship_id: uuid.UUID | None = None
    containment: float | None = None
    evidence: list[str] = []
    alternatives: list[JoinAlternativeOut] = []


class JoinPathStepOut(OrmModel):
    from_column: str
    to_column: str


class JoinPathOut(OrmModel):
    """How two of a control's tables connect, possibly through bridge tables.
    Shown for the auditor's understanding; tests still join two tables directly."""

    from_table: str
    to_table: str
    steps: list[JoinPathStepOut]
    executed: bool = False


class JoinReportOut(OrmModel):
    joins: list[JoinResolutionOut]
    paths: list[JoinPathOut]
    # True when any join is contradicted by the data: the rule is not generated
    # automatically until a person reviews it.
    blocking: bool = False


class RelationshipRuling(OrmModel):
    status: Literal["confirmed", "rejected", "detected"]
