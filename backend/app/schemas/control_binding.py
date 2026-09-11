import uuid
from datetime import datetime
from typing import Literal

from app.schemas.common import OrmModel

BindingStatus = Literal["bound", "not_applicable"]


class ControlTableBindingCreate(OrmModel):
    canonical_table_name: str
    data_source_id: uuid.UUID
    entity_id: uuid.UUID


class ControlTableNotApplicable(OrmModel):
    canonical_table_name: str
    reason: str


class ControlTableBindingOut(OrmModel):
    binding_id: uuid.UUID
    organization_id: uuid.UUID
    control_id: uuid.UUID
    canonical_table_name: str
    data_source_id: uuid.UUID | None
    entity_id: uuid.UUID | None
    entity_name: str | None = None
    source_name: str | None = None
    status: BindingStatus
    not_applicable_reason: str | None
    bound_by: uuid.UUID | None
    bound_at: datetime


class TableBindingProgressOut(OrmModel):
    required_tables: list[str]
    bindings: list[ControlTableBindingOut]
    total: int
    satisfied: int
    ready: bool


class ControlRuleTemplateOut(OrmModel):
    template_id: uuid.UUID
    control_library_id: uuid.UUID
    rule_name: str
    rule_definition: dict
