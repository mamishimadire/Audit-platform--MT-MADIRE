import uuid
from datetime import datetime

from app.schemas.common import OrmModel


class MappingSuggestion(OrmModel):
    field_id: uuid.UUID
    field_name: str
    data_type: str | None
    suggested_canonical_field: str
    confidence_score: float


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
