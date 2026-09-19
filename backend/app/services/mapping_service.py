import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.canonical_model import (
    LOW_CONTENT_FIT_THRESHOLD,
    infer_object_for_entity,
    mapping_status_for_confidence,
    suggest_canonical_field,
)
from app.core.join_requirements import required_join_key_fields
from app.core.value_profile import (
    LOW_VALUE_FIT_THRESHOLD,
    VALUE_CONTRADICTION_CONFIDENCE_CAP,
    score_table_fit,
    score_value_fit,
)
from app.models.audit_test import ControlAuditTest, TestDataMapping, TestRule
from app.models.control_library import ControlRuleTemplate
from app.models.data_source import DataEntity, DataField, DataSource
from app.models.risk_control import Control
from app.schemas.data_mapping import (
    MappingSuggestion,
    RequiredFieldStatus,
    RequiredObjectStatus,
    MappingReadinessOut,
    TestDataMappingCreate,
)
from app.schemas.test_rule import required_fields_by_object_for
from app.services.audit_log_service import log_action
from app.services.column_info_service import columns_for_entities


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


def _join_flags_for_entity(db: Session, *, audit_test_id: uuid.UUID, entity_id: uuid.UUID) -> dict[uuid.UUID, str]:
    """field_id -> reason, for this table's columns that serve as a join key the
    data does not support (contradicted) or that has more than one plausible
    pairing (ambiguous). Reads stored relationship evidence only."""
    from app.services.join_resolution_service import build_join_report

    mine = {f.field_id for f in db.scalars(select(DataField).where(DataField.entity_id == entity_id))}
    flags: dict[uuid.UUID, str] = {}
    for join in build_join_report(db, audit_test_id=audit_test_id).joins:
        if join.verdict not in ("contradicted", "ambiguous"):
            continue
        involved = [c for c in (join.left, join.right) if c is not None]
        for alt in join.alternatives:
            involved.extend([alt.left, alt.right])
        for c in involved:
            if c.field_id in mine:
                flags.setdefault(c.field_id, f"{join.requires_left} ↔ {join.requires_right}: {join.reason}")
    return flags


def suggest_mappings_for_entity(
    db: Session, *, entity_id: uuid.UUID, audit_test_id: uuid.UUID | None = None
) -> list[MappingSuggestion]:
    entity = db.get(DataEntity, entity_id)
    preferred_object = infer_object_for_entity(entity.entity_name) if entity else None

    fields = list(db.scalars(select(DataField).where(DataField.entity_id == entity_id)))
    columns = columns_for_entities(db, [entity_id]).get(entity_id, [])
    info_by_name = {c.name: c for c in columns}
    # The table's NAME is what suggested preferred_object, and that hint
    # boosts every column into it. If the table's own columns barely resemble
    # that object (an unrelated table that shares a common name) — by their
    # names, or by what they actually hold — don't let the name vouch for it:
    # fall back to the unconstrained, already-capped search instead of
    # confidently boosting into the wrong object.
    if preferred_object is not None and entity is not None and columns:
        fit = score_table_fit(columns, entity.entity_name)
        if fit is not None and fit.effective is not None and fit.effective < LOW_CONTENT_FIT_THRESHOLD:
            preferred_object = None
    # With a control in view, a column serving as a join key is also judged by
    # whether the relationship it is meant to carry actually holds.
    join_flags = _join_flags_for_entity(db, audit_test_id=audit_test_id, entity_id=entity_id) if audit_test_id else {}
    suggestions = []
    for field in fields:
        canonical_field, confidence = suggest_canonical_field(
            field.field_name, is_primary_key=field.is_primary_key, preferred_object=preferred_object
        )
        value_fit: float | None = None
        value_reason: str | None = None
        info = info_by_name.get(field.field_name)
        if canonical_field and info is not None:
            value_fit, value_reason = score_value_fit(canonical_field, data_type=info.data_type, profile=info.profile)
            # A name can't tell that a column holds the wrong KIND of data
            # (genre text under "status", free text under "last_login").
            # Demote so it can't auto-accept and a person confirms it; never
            # raise a score on a good value fit — names stay primary.
            if value_fit is not None and value_fit < LOW_VALUE_FIT_THRESHOLD:
                confidence = min(confidence, VALUE_CONTRADICTION_CONFIDENCE_CAP)
        relationship_reason = join_flags.get(field.field_id) if canonical_field else None
        if relationship_reason:
            # Never auto-accepted: a person confirms which pair really carries the join.
            confidence = min(confidence, VALUE_CONTRADICTION_CONFIDENCE_CAP)
        suggestions.append(
            MappingSuggestion(
                field_id=field.field_id,
                field_name=field.field_name,
                data_type=field.data_type,
                suggested_canonical_field=canonical_field,
                confidence_score=confidence,
                value_fit_score=value_fit,
                value_fit_reason=value_reason if value_fit is not None and value_fit < LOW_VALUE_FIT_THRESHOLD else None,
                relationship_reason=relationship_reason,
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


def get_mapping_status_for_tests(db: Session, *, audit_test_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """One of "not_mapped" / "pending_approval" / "rejected" / "approved"
    per test — a handful of queries total regardless of test count, same
    batching pattern as audit_test_service.describe_tests (see its own
    docstring on why: N round trips for an N-test list turns a sub-second
    load into a multi-second one).

    "approved" requires BOTH that every field the test's own rule (or,
    if none is active yet, its control's template) actually reads is
    mapped, AND that every one of those mappings is approved — not just
    that whichever mappings happen to exist are all approved. The first
    version of this function only checked the latter, which reported
    "Mapped & approved" for a test that had genuinely never mapped a
    field its rule reads at all (MD-004: payments.supplier_id was never
    mapped, but the one field that WAS mapped — payments.payment_id —
    was approved, so every EXISTING mapping was "approved" even though
    the rule was nowhere near ready). A test with no active rule and no
    template at all has no fields to check completeness against, so it
    falls back to "every existing mapping is approved" — there's no
    contract yet to be complete against. reject_mapping doesn't set a
    literal "rejected" mapping_status (it resets to "needs_review" so
    the field stays editable — see its own docstring); "rejected" here
    means specifically needs_review rows carrying an unresolved
    rejected_at, the same signal the mapping screen itself uses to show
    why a row was kicked back.
    """
    if not audit_test_ids:
        return {}

    mapping_rows = db.execute(
        select(TestDataMapping.audit_test_id, TestDataMapping.canonical_field, TestDataMapping.mapping_status, TestDataMapping.rejected_at).where(
            TestDataMapping.audit_test_id.in_(audit_test_ids),
            TestDataMapping.mapping_status != "superseded",
        )
    ).all()
    mappings_by_test: dict[uuid.UUID, list[tuple[str | None, str, object]]] = {}
    for test_id, canonical_field, status, rejected_at in mapping_rows:
        mappings_by_test.setdefault(test_id, []).append((canonical_field, status, rejected_at))

    # Active rule's own rule_definition wins when one exists; otherwise
    # fall back to the control's template — same precedence
    # get_control_rule_template uses for one test at a time.
    active_rules = db.execute(
        select(TestRule.audit_test_id, TestRule.rule_definition).where(
            TestRule.audit_test_id.in_(audit_test_ids), TestRule.status == "active"
        )
    ).all()
    rule_definition_by_test: dict[uuid.UUID, dict] = {test_id: json.loads(rule_def) for test_id, rule_def in active_rules}

    remaining_ids = [tid for tid in audit_test_ids if tid not in rule_definition_by_test]
    if remaining_ids:
        links = db.execute(
            select(ControlAuditTest.audit_test_id, ControlAuditTest.control_id).where(ControlAuditTest.audit_test_id.in_(remaining_ids))
        ).all()
        control_id_by_test = dict(links)
        controls = (
            db.scalars(select(Control).where(Control.control_id.in_(control_id_by_test.values()))) if control_id_by_test else []
        )
        library_id_by_control = {c.control_id: c.control_library_id for c in controls if c.control_library_id is not None}
        library_ids = set(library_id_by_control.values())
        templates = (
            db.scalars(select(ControlRuleTemplate).where(ControlRuleTemplate.control_library_id.in_(library_ids))) if library_ids else []
        )
        template_by_library_id = {t.control_library_id: t for t in templates}
        for test_id in remaining_ids:
            control_id = control_id_by_test.get(test_id)
            library_id = library_id_by_control.get(control_id) if control_id else None
            template = template_by_library_id.get(library_id) if library_id else None
            if template is not None:
                rule_definition_by_test[test_id] = json.loads(template.rule_definition)

    result: dict[uuid.UUID, str] = {}
    for test_id in audit_test_ids:
        mappings = mappings_by_test.get(test_id)
        if not mappings:
            result[test_id] = "not_mapped"
            continue
        if any(status == "needs_review" and rejected_at is not None for _, status, rejected_at in mappings):
            result[test_id] = "rejected"
            continue

        rule_definition = rule_definition_by_test.get(test_id)
        if rule_definition is not None:
            required = required_fields_by_object_for(rule_definition)
            required_canonical_fields = {f"{obj}.{field}" for obj, fields in required.items() for field in fields}
            approved_canonical_fields = {canonical_field for canonical_field, status, _ in mappings if status == "approved"}
            result[test_id] = "approved" if required_canonical_fields <= approved_canonical_fields else "pending_approval"
        else:
            result[test_id] = "approved" if all(status == "approved" for _, status, _ in mappings) else "pending_approval"
    return result


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
    join_keys = required_join_key_fields(rule_definition)
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
                field_statuses.append(
                    RequiredFieldStatus(canonical_field=field_name, mapped=False, is_join_key=field_name in join_keys.get(canonical_object, set()))
                )
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
                        is_join_key=field_name in join_keys.get(canonical_object, set()),
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


def get_template_requirements(db: Session, *, audit_test_id: uuid.UUID) -> MappingReadinessOut:
    """What THIS control's rule template needs, independent of whether a
    rule has actually been generated yet — lets the mapping screen show
    "required for this test" vs. everything else discovered, before
    "Generate from control template" is even clicked. A control with no
    template (137 of 157 today — see migration 0027's own docstring on why
    most controls deliberately have none yet) has no contract to narrow
    the screen down to, so has_rule=False with no objects means exactly
    that: every discovered column stays visible, same as before this
    existed, rather than guessing which ones matter."""
    from app.services.test_rule_service import get_control_rule_template

    _, template = get_control_rule_template(db, audit_test_id=audit_test_id)
    if template is None:
        return MappingReadinessOut(has_rule=False, ready=False, objects=[])
    return _readiness_for_definition(db, audit_test_id=audit_test_id, rule_definition=json.loads(template.rule_definition))


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
    _maybe_auto_generate_rule(db, audit_test_id=mapping.audit_test_id, organization_id=organization_id)
    return mapping


def _maybe_auto_generate_rule(db: Session, *, audit_test_id: uuid.UUID, organization_id: uuid.UUID) -> None:
    """Once mapping becomes fully ready against a control's rule template,
    the rule is generated automatically instead of waiting for someone to
    click "Generate from control template" — every field the template
    needs is already approved the moment this runs, so there is nothing
    left for a human to decide before the rule can exist. It still lands
    as status='pending_approval' like every other path into
    generate_rule_from_template: the platform writing the rule from a
    template is not the same as an auditor reviewing it for this specific
    client's mapping."""
    from app.services.test_rule_service import generate_rule_from_template

    readiness = get_template_requirements(db, audit_test_id=audit_test_id)
    if not readiness.has_rule or not readiness.ready:
        return
    # A join the data contradicts must not silently become a live test: a person
    # reviews it first (they can still generate the rule by hand once they have).
    from app.services.join_resolution_service import build_join_report

    if build_join_report(db, audit_test_id=audit_test_id).blocking:
        return
    existing = db.scalar(
        select(TestRule).where(TestRule.audit_test_id == audit_test_id, TestRule.status.in_(("pending_approval", "active")))
    )
    if existing is not None:
        return
    try:
        generate_rule_from_template(db, audit_test_id=audit_test_id, organization_id=organization_id, created_by_user_id=None)
    except ValueError:
        pass  # a precondition generate_rule_from_template itself checks isn't met yet — stays a manual step


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
