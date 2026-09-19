"""
Relationship validation (product feedback: a join_field mapped correctly
by NAME doesn't guarantee the two sides actually share the same values —
"employee_id" existing on both tables proves nothing about whether they
reference the same employees). This queries each side's live data and
reports how much the two value sets genuinely overlap, so a mapping that
LOOKS right can be told apart from one that IS right.

Deliberately advisory, not a hard gate on activation: real client data is
often legitimately messy (soft-deleted rows, a partial export, an
intentionally filtered secondary table), so a low match rate is surfaced
prominently for the auditor to judge rather than silently blocking
"Generate from control template."
"""
import uuid

from sqlalchemy.orm import Session

from app.core.join_requirements import join_requirements_for
from app.models.audit_test import TestDataMapping
from app.models.data_source import DataEntity
from app.schemas.data_mapping import RelationshipCheckOut
from app.services.data_source_service import sample_distinct_values
from app.services.mapping_service import list_mappings

# Below this, something is more likely wrong with the mapping itself than
# with real-world data messiness — worth a visible warning, not a silent pass.
_MIN_HEALTHY_MATCH_RATE = 50.0


def required_joins_for(rule_definition: dict) -> list[tuple[str, str, str, str]]:
    """(left_object, right_object, left_field, right_field) for every join the
    rule performs — all primitives, gate and bridge hops included (delegates to
    app.core.join_requirements, the single definition of "what a rule joins")."""
    return [(j.left_object, j.right_object, j.left_field, j.right_field) for j in join_requirements_for(rule_definition)]


def _mapped_physical_field(mappings: list[TestDataMapping], canonical_field: str) -> TestDataMapping | None:
    for m in mappings:
        if m.canonical_field == canonical_field and m.entity_id is not None and m.field_id is not None:
            return m
    return None


def _sample_for_mapping(db: Session, mapping: TestDataMapping) -> set[str] | None:
    entity = db.get(DataEntity, mapping.entity_id)
    if entity is None:
        return None
    from app.models.data_source import DataField

    field = db.get(DataField, mapping.field_id)
    if field is None:
        return None
    return sample_distinct_values(db, data_source_id=mapping.data_source_id, entity_name=entity.entity_name, field_name=field.field_name)


def validate_relationships(db: Session, *, audit_test_id: uuid.UUID, rule_definition: dict) -> list[RelationshipCheckOut]:
    joins = required_joins_for(rule_definition)
    if not joins:
        return []

    mappings = list_mappings(db, audit_test_id=audit_test_id)
    results: list[RelationshipCheckOut] = []
    for primary_object, secondary_object, join_field, secondary_join_field in joins:
        primary_mapping = _mapped_physical_field(mappings, f"{primary_object}.{join_field}")
        secondary_mapping = _mapped_physical_field(mappings, f"{secondary_object}.{secondary_join_field}")
        if primary_mapping is None or secondary_mapping is None:
            results.append(
                RelationshipCheckOut(
                    primary_object=primary_object,
                    secondary_object=secondary_object,
                    join_field=join_field,
                    secondary_join_field=secondary_join_field,
                    primary_sample_count=0,
                    secondary_sample_count=0,
                    overlap_count=0,
                    match_rate=0.0,
                    status="not_available",
                    detail=f"Map both {primary_object}.{join_field} and {secondary_object}.{secondary_join_field} first.",
                )
            )
            continue

        primary_values = _sample_for_mapping(db, primary_mapping)
        secondary_values = _sample_for_mapping(db, secondary_mapping)
        if primary_values is None or secondary_values is None:
            results.append(
                RelationshipCheckOut(
                    primary_object=primary_object,
                    secondary_object=secondary_object,
                    join_field=join_field,
                    secondary_join_field=secondary_join_field,
                    primary_sample_count=0,
                    secondary_sample_count=0,
                    overlap_count=0,
                    match_rate=0.0,
                    status="not_available",
                    detail="Live relationship validation isn't available for this data source yet — it needs a direct (non-Gateway) connection.",
                )
            )
            continue

        overlap = len(primary_values & secondary_values)
        smaller = min(len(primary_values), len(secondary_values)) or 1
        match_rate = round(overlap / smaller * 100, 1)
        status = "validated" if match_rate >= _MIN_HEALTHY_MATCH_RATE else "weak"
        results.append(
            RelationshipCheckOut(
                primary_object=primary_object,
                secondary_object=secondary_object,
                join_field=join_field,
                secondary_join_field=secondary_join_field,
                primary_sample_count=len(primary_values),
                secondary_sample_count=len(secondary_values),
                overlap_count=overlap,
                match_rate=match_rate,
                status=status,
                detail=(
                    f"{overlap} of {smaller} sampled values overlap between the two tables."
                    if status == "validated"
                    else f"Only {overlap} of {smaller} sampled values overlap — double check these two fields really represent the same business key."
                ),
            )
        )
    return results
