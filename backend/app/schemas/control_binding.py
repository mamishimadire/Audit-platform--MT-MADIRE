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
    # Same second signal the suggestions carry, for the table actually bound —
    # so a hand-picked (or long-ago bound) table whose columns don't resemble
    # what the control needs is flagged too. None = not applicable.
    content_fit_score: float | None = None


class TableBindingSuggestionOut(OrmModel):
    """A discovered table the platform thinks is likely the physical table
    behind a control's required canonical name, ranked by name similarity —
    see app.core.canonical_model.score_table_name_match. Purely a hint: the
    auditor still picks from the normal dropdowns, nothing here writes a
    binding on its own."""

    entity_id: uuid.UUID
    entity_name: str
    data_source_id: uuid.UUID
    source_name: str | None = None
    confidence_score: float
    # Name match alone can't tell a real table from an unrelated one that
    # shares its name — this is the independent second signal (see
    # canonical_model.score_table_content_fit). None = not applicable.
    content_fit_score: float | None = None


class TableBindingProgressOut(OrmModel):
    required_tables: list[str]
    bindings: list[ControlTableBindingOut]
    total: int
    satisfied: int
    ready: bool
    # Only present for required tables that aren't bound yet — never
    # recomputed/shown for one that's already bound, since a real binding
    # is always the stronger signal.
    suggestions: dict[str, list[TableBindingSuggestionOut]] = {}


class ControlRuleTemplateOut(OrmModel):
    template_id: uuid.UUID
    control_library_id: uuid.UUID
    rule_name: str
    rule_definition: dict
