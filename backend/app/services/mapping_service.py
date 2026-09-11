import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.canonical_model import infer_object_for_entity, mapping_status_for_confidence, suggest_canonical_field
from app.models.audit_test import TestDataMapping, TestRule
from app.models.data_source import DataEntity, DataField, DataSource
from app.schemas.data_mapping import (
    MappingSuggestion,
    RequiredFieldStatus,
    RequiredObjectStatus,
    MappingReadinessOut,
    TestDataMappingCreate,
)
from app.schemas.test_rule import required_fields_by_object_for
from app.services.audit_log_service import log_action


def _flag_rules_needing_review(db: Session, *, audit_test_id: uuid.UUID, changed_canonical_field: str) -> None:
    """
    A rule keeps running on whatever it last resolved — remapping or
    unmapping a field it reads must never silently change what a live test
    is checking, or silently leave it broken. Flagging for review is the
    honest middle ground: the auditor is told, the rule doesn't change
    behavior on its own either way.
    """
    if "." not in changed_canonical_field:
        return
    canonical_object, canonical_field = changed_canonical_field.split(".", 1)
    rules = db.scalars(select(TestRule).where(TestRule.audit_test_id == audit_test_id, TestRule.status == "active"))
    for rule in rules:
        required = required_fields_by_object_for(json.loads(rule.rule_definition))
        if canonical_field in required.get(canonical_object, set()):
            rule.needs_review = True


def suggest_mappings_for_entity(db: Session, *, entity_id: uuid.UUID) -> list[MappingSuggestion]:
    entity = db.get(DataEntity, entity_id)
    preferred_object = infer_object_for_entity(entity.entity_name) if entity else None

    fields = db.scalars(select(DataField).where(DataField.entity_id == entity_id))
    suggestions = []
    for field in fields:
        canonical_field, confidence = suggest_canonical_field(
            field.field_name, is_primary_key=field.is_primary_key, preferred_object=preferred_object
        )
        suggestions.append(
            MappingSuggestion(
                field_id=field.field_id,
                field_name=field.field_name,
                data_type=field.data_type,
                suggested_canonical_field=canonical_field,
                confidence_score=confidence,
            )
        )
    return suggestions


def _retire_existing_mapping(db: Session, *, existing: TestDataMapping, organization_id: uuid.UUID, actor_user_id: uuid.UUID) -> int:
    """A canonical_field can only ever resolve to one physical column per
    test — two mappings claiming the same one is not "extra data," it's
    ambiguous, and the test engine would silently pick whichever happened
    to iterate last. Replacing it either hard-deletes (nothing was ever
    approved, so there's no history worth keeping) or supersedes (an
    approved mapping's history must survive being replaced, same as
    editing one — see update_mapping_field). Returns the version number
    the replacement should use."""
    log_action(
        db,
        action=f"Replaced mapping for '{existing.canonical_field}'",
        organization_id=organization_id,
        user_id=actor_user_id,
        entity_type="test_data_mappings",
        entity_id=existing.mapping_id,
        old_value={"field_id": str(existing.field_id) if existing.field_id else None, "mapping_status": existing.mapping_status},
    )
    if existing.mapping_status == "approved":
        existing.mapping_status = "superseded"
        db.flush()
        return existing.version + 1
    db.delete(existing)
    db.flush()
    return 1


def create_mapping(
    db: Session,
    *,
    audit_test_id: uuid.UUID,
    payload: TestDataMappingCreate,
    organization_id: uuid.UUID,
    created_by_user_id: uuid.UUID,
) -> TestDataMapping:
    existing = db.scalar(
        select(TestDataMapping).where(
            TestDataMapping.audit_test_id == audit_test_id,
            TestDataMapping.canonical_field == payload.canonical_field,
            TestDataMapping.mapping_status != "superseded",
        )
    )
    version = 1
    supersedes_mapping_id = existing.mapping_id if existing is not None else None
    if existing is not None:
        version = _retire_existing_mapping(db, existing=existing, organization_id=organization_id, actor_user_id=created_by_user_id)
        if version == 1:
            supersedes_mapping_id = None  # existing was hard-deleted, not superseded — nothing to link back to

    confidence = payload.confidence_score if payload.confidence_score is not None else 0.0
    status = "auto" if confidence >= 90 else "needs_review"
    mapping = TestDataMapping(
        audit_test_id=audit_test_id,
        data_source_id=payload.data_source_id,
        entity_id=payload.entity_id,
        field_id=payload.field_id,
        canonical_field=payload.canonical_field,
        confidence_score=confidence,
        mapping_status=status,
        created_by=created_by_user_id,
        version=version,
        supersedes_mapping_id=supersedes_mapping_id,
    )
    db.add(mapping)
    db.flush()
    log_action(
        db,
        action=f"Mapped field to '{mapping.canonical_field}'",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="test_data_mappings",
        entity_id=mapping.mapping_id,
        new_value={"canonical_field": mapping.canonical_field, "mapping_status": mapping.mapping_status},
    )
    db.commit()
    db.refresh(mapping)
    return mapping


def list_mappings(db: Session, *, audit_test_id: uuid.UUID) -> list[TestDataMapping]:
    """Current, live mappings only — superseded versions are excluded since
    every functional caller (readiness computation, the mapping grid,
    execution) wants "what's true now," not history. Use
    list_mapping_history for the full version chain."""
    return list(
        db.scalars(
            select(TestDataMapping).where(
                TestDataMapping.audit_test_id == audit_test_id, TestDataMapping.mapping_status != "superseded"
            )
        )
    )


def list_mapping_history(db: Session, *, audit_test_id: uuid.UUID) -> list[TestDataMapping]:
    """Every version ever created for this test, including superseded ones
    — what an execution recorded at the time can be reconciled against
    exactly what was approved then, not just what's approved today."""
    return list(
        db.scalars(
            select(TestDataMapping)
            .where(TestDataMapping.audit_test_id == audit_test_id)
            .order_by(TestDataMapping.canonical_field, TestDataMapping.version)
        )
    )


def update_mapping_field(db: Session, *, mapping: TestDataMapping, canonical_field: str, updated_by_user_id: uuid.UUID | None = None) -> TestDataMapping:
    """A human overriding the suggestion is always 'manually_mapped', regardless of what confidence produced it.

    Immutability: an APPROVED mapping is never edited in place — that
    would let its target column change after sign-off with no new review.
    Editing one instead creates a new version (supersedes_mapping_id back
    to this row, this row's status flipped to 'superseded'). A mapping
    that was never approved (needs_review/auto/manually_mapped/rejected)
    has nothing live to protect, so editing in place is still fine.
    """
    actor = updated_by_user_id or mapping.created_by

    # Same one-mapping-per-canonical-field rule as create_mapping — editing
    # into a field another mapping already claims must not silently create
    # the same ambiguity from the other direction.
    conflicting = db.scalar(
        select(TestDataMapping).where(
            TestDataMapping.audit_test_id == mapping.audit_test_id,
            TestDataMapping.canonical_field == canonical_field,
            TestDataMapping.mapping_id != mapping.mapping_id,
            TestDataMapping.mapping_status != "superseded",
        )
    )
    if conflicting is not None:
        if conflicting.mapping_status == "approved":
            conflicting.mapping_status = "superseded"
        else:
            db.delete(conflicting)

    old_canonical_field = mapping.canonical_field

    if mapping.mapping_status == "approved":
        new_mapping = TestDataMapping(
            audit_test_id=mapping.audit_test_id,
            data_source_id=mapping.data_source_id,
            entity_id=mapping.entity_id,
            field_id=mapping.field_id,
            canonical_field=canonical_field,
            confidence_score=mapping.confidence_score,
            mapping_status="manually_mapped",
            created_by=actor,
            version=mapping.version + 1,
            supersedes_mapping_id=mapping.mapping_id,
        )
        db.add(new_mapping)
        db.flush()
        mapping.mapping_status = "superseded"
        if old_canonical_field and old_canonical_field != canonical_field:
            _flag_rules_needing_review(db, audit_test_id=mapping.audit_test_id, changed_canonical_field=old_canonical_field)
        db.commit()
        db.refresh(new_mapping)
        return new_mapping

    mapping.canonical_field = canonical_field
    mapping.mapping_status = "manually_mapped"
    mapping.approved_by = None
    mapping.approved_at = None
    if old_canonical_field and old_canonical_field != canonical_field:
        _flag_rules_needing_review(db, audit_test_id=mapping.audit_test_id, changed_canonical_field=old_canonical_field)
    db.commit()
    db.refresh(mapping)
    return mapping


def delete_mapping(db: Session, *, mapping: TestDataMapping, organization_id: uuid.UUID, deleted_by_user_id: uuid.UUID) -> None:
    """Unmap: a column that was wrongly matched (or a table swapped out for
    a different one) needs to be cleanly removable, not just overwritable —
    the test engine must never run against a stale, half-wrong mapping."""
    log_action(
        db,
        action=f"Unmapped '{mapping.canonical_field}'",
        organization_id=organization_id,
        user_id=deleted_by_user_id,
        entity_type="test_data_mappings",
        entity_id=mapping.mapping_id,
        old_value={"canonical_field": mapping.canonical_field},
    )
    if mapping.canonical_field:
        _flag_rules_needing_review(db, audit_test_id=mapping.audit_test_id, changed_canonical_field=mapping.canonical_field)
    db.delete(mapping)
    db.commit()


def _readiness_for_definition(db: Session, *, audit_test_id: uuid.UUID, rule_definition: dict) -> MappingReadinessOut:
    """Shared core: given ANY rule_definition (an existing active rule's, or
    a control's template — before a rule even exists yet), compute which
    canonical objects/fields it needs and which are mapped already."""
    required = required_fields_by_object_for(rule_definition)
    existing_mappings = list_mappings(db, audit_test_id=audit_test_id)

    objects: list[RequiredObjectStatus] = []
    overall_ready = True
    for canonical_object, fields in required.items():
        # A mapping's canonical_field is stored as "object.field" — find every
        # mapping belonging to this object regardless of which table it points at.
        object_mappings = {
            m.canonical_field.split(".", 1)[1]: m
            for m in existing_mappings
            if m.canonical_field and "." in m.canonical_field and m.canonical_field.split(".", 1)[0] == canonical_object
        }

        field_statuses = []
        for field_name in sorted(fields):
            m = object_mappings.get(field_name)
            if m is None:
                field_statuses.append(RequiredFieldStatus(canonical_field=field_name, mapped=False))
                overall_ready = False
            else:
                physical_field = db.get(DataField, m.field_id) if m.field_id else None
                field_statuses.append(
                    RequiredFieldStatus(
                        canonical_field=field_name,
                        mapped=True,
                        mapping_id=m.mapping_id,
                        field_name=physical_field.field_name if physical_field else None,
                        mapping_status=m.mapping_status,
                    )
                )

        fully_mapped = all(f.mapped for f in field_statuses)
        # Entity/source shown for this object come from whichever mapping
        # exists for it — if none do yet, both stay unset (nothing chosen).
        sample_mapping = next(iter(object_mappings.values()), None)
        entity = db.get(DataEntity, sample_mapping.entity_id) if sample_mapping else None
        source = db.get(DataSource, sample_mapping.data_source_id) if sample_mapping else None

        objects.append(
            RequiredObjectStatus(
                canonical_object=canonical_object,
                entity_id=entity.entity_id if entity else None,
                entity_name=entity.entity_name if entity else None,
                data_source_id=source.data_source_id if source else None,
                source_name=source.source_name if source else None,
                required_fields=field_statuses,
                fully_mapped=fully_mapped,
            )
        )

    return MappingReadinessOut(has_rule=True, ready=overall_ready, objects=objects)


def get_mapping_readiness(db: Session, *, audit_test_id: uuid.UUID) -> MappingReadinessOut:
    """
    For the test's active rule: which canonical objects (tables) does it
    need, which specific fields on each actually matter for testing (not
    every column a discovered table happens to have), and which of those
    are mapped already — so the mapping screen can show exactly what's
    outstanding instead of a flat list of every discovered field.
    """
    rule = db.scalar(select(TestRule).where(TestRule.audit_test_id == audit_test_id, TestRule.status == "active"))
    if rule is None:
        return MappingReadinessOut(has_rule=False, ready=False, objects=[])
    return _readiness_for_definition(db, audit_test_id=audit_test_id, rule_definition=json.loads(rule.rule_definition))


def get_readiness_against_template(db: Session, *, audit_test_id: uuid.UUID, rule_definition: dict) -> MappingReadinessOut:
    """Same computation, but against a control's rule template rather than
    an existing rule — used to decide whether auto-generation is possible
    yet, and to preview required fields before any rule exists."""
    return _readiness_for_definition(db, audit_test_id=audit_test_id, rule_definition=rule_definition)


def approve_mapping(db: Session, *, mapping: TestDataMapping, approved_by_user_id: uuid.UUID, organization_id: uuid.UUID) -> TestDataMapping:
    """
    The evidence-trail requirement from Section 12: mapping approval is
    logged against the reviewing auditor's identity and timestamp.

    Maker-checker: whoever created (or last manually edited) this mapping
    cannot also be the one approving it — a segregation-of-duties control,
    not a permission check (both people can easily hold the exact same
    audit_framework:manage permission).
    """
    if mapping.created_by is not None and mapping.created_by == approved_by_user_id:
        raise ValueError("You mapped this field yourself — a different authorized user must approve it.")

    mapping.mapping_status = "approved"
    mapping.approved_by = approved_by_user_id
    mapping.approved_at = datetime.now(timezone.utc)
    mapping.rejected_by = None
    mapping.rejected_at = None
    mapping.rejected_reason = None
    log_action(
        db,
        action=f"Approved mapping to '{mapping.canonical_field}'",
        organization_id=organization_id,
        user_id=approved_by_user_id,
        entity_type="test_data_mappings",
        entity_id=mapping.mapping_id,
    )
    db.commit()
    db.refresh(mapping)
    return mapping


def reject_mapping(db: Session, *, mapping: TestDataMapping, reason: str, rejected_by_user_id: uuid.UUID, organization_id: uuid.UUID) -> TestDataMapping:
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reject a mapping.")
    mapping.mapping_status = "needs_review"
    mapping.approved_by = None
    mapping.approved_at = None
    mapping.rejected_by = rejected_by_user_id
    mapping.rejected_at = datetime.now(timezone.utc)
    mapping.rejected_reason = reason
    log_action(
        db,
        action=f"Rejected mapping to '{mapping.canonical_field}': {reason}",
        organization_id=organization_id,
        user_id=rejected_by_user_id,
        entity_type="test_data_mappings",
        entity_id=mapping.mapping_id,
        new_value={"mapping_status": "needs_review", "reason": reason},
    )
    db.commit()
    db.refresh(mapping)
    return mapping
