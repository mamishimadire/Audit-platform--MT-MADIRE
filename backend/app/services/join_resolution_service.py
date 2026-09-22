"""
Loads what the platform knows (a control's rule, its bound tables, the
discovered columns, constraints, profiles and the relationship graph) and asks
app.core.join_resolution which columns satisfy each join the rule needs.

Everything here reads stored data only: no call to the client's database, so it
is cheap enough to run whenever mapping suggestions are requested. The live
measurements happened earlier, in data_source_service.infer_relationships_for_source.
"""
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.canonical_model import infer_object_for_entity
from app.core.join_requirements import join_requirements_for
from app.core.join_resolution import (
    CONTRADICTED,
    Edge,
    JoinResolution,
    UNRESOLVED,
    find_paths,
    resolve_join,
)
from app.core.relationship_inference import ColumnRef
from app.models.audit_test import ControlAuditTest, TestDataMapping, TestRule
from app.models.control_library import ControlRuleTemplate, ControlTableBinding
from app.models.data_source import DataEntity, DataField, DataFieldConstraint, DataFieldProfile, DataRelationship
from app.models.risk_control import Control
from app.schemas.data_mapping import (
    JoinAlternativeOut,
    JoinColumnOut,
    JoinPathOut,
    JoinPathStepOut,
    JoinReportOut,
    JoinResolutionOut,
)


def _rule_definition_for_test(db: Session, audit_test_id: uuid.UUID) -> tuple[Control | None, dict | None]:
    """The active rule if one exists, else the control's template (so joins can
    be checked before a rule has even been generated)."""
    link = db.scalar(select(ControlAuditTest).where(ControlAuditTest.audit_test_id == audit_test_id))
    if link is None:
        return None, None
    control = db.get(Control, link.control_id)
    active = db.scalar(select(TestRule).where(TestRule.audit_test_id == audit_test_id, TestRule.status == "active"))
    if active is not None:
        return control, json.loads(active.rule_definition)
    if control is None or control.control_library_id is None:
        return control, None
    template = db.scalar(select(ControlRuleTemplate).where(ControlRuleTemplate.control_library_id == control.control_library_id))
    return control, (json.loads(template.rule_definition) if template is not None else None)


def _object_entities(db: Session, control: Control | None, audit_test_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """canonical object -> the physical table serving it, from the control's
    table bindings first, then from any mapping already made."""
    mapping: dict[str, uuid.UUID] = {}
    if control is not None:
        for binding in db.scalars(
            select(ControlTableBinding).where(ControlTableBinding.control_id == control.control_id, ControlTableBinding.status == "bound")
        ):
            if binding.entity_id is None:
                continue
            obj = infer_object_for_entity(binding.canonical_table_name) or binding.canonical_table_name
            mapping.setdefault(obj, binding.entity_id)
    for m in db.scalars(
        select(TestDataMapping).where(TestDataMapping.audit_test_id == audit_test_id, TestDataMapping.mapping_status != "superseded")
    ):
        if m.entity_id is not None and m.canonical_field and "." in m.canonical_field:
            mapping.setdefault(m.canonical_field.split(".", 1)[0], m.entity_id)
    return mapping


def _column_rows(db: Session, where_clause) -> list[ColumnRef]:
    rows = db.execute(
        select(DataField, DataEntity.entity_name, DataFieldConstraint, DataFieldProfile)
        .join(DataEntity, DataEntity.entity_id == DataField.entity_id)
        .outerjoin(DataFieldConstraint, DataFieldConstraint.field_id == DataField.field_id)
        .outerjoin(DataFieldProfile, DataFieldProfile.field_id == DataField.field_id)
        .where(where_clause)
    )
    return [
        ColumnRef(
            field_id=str(f.field_id), entity_id=str(f.entity_id), entity_name=entity_name, name=f.field_name,
            data_type=f.data_type, is_primary_key=bool(f.is_primary_key),
            is_unique=bool(c.is_unique) if c is not None else False,
            distinct_ratio=p.distinct_ratio if p is not None else None,
            sample_size=p.sample_size if p is not None else 0,
            null_ratio=p.null_ratio if p is not None else None,
        )
        for f, entity_name, c, p in rows
    ]


def _load_columns(db: Session, entity_ids: list[uuid.UUID]) -> list[ColumnRef]:
    return _column_rows(db, DataField.entity_id.in_(entity_ids)) if entity_ids else []


def _load_edges_for_sources(db: Session, entity_ids: list[uuid.UUID]) -> list[Edge]:
    """Every stored relationship in the data source(s) these tables live in —
    including edges between OTHER tables, which is what lets a path run through
    a bridge table (users <- user_roles -> roles)."""
    if not entity_ids:
        return []
    sources = set(db.scalars(select(DataEntity.data_source_id).where(DataEntity.entity_id.in_(entity_ids))))
    rows = db.scalars(select(DataRelationship).where(DataRelationship.data_source_id.in_(sources)))
    return [
        Edge(
            child_field_id=str(r.child_field_id), parent_field_id=str(r.parent_field_id), kind=r.kind, status=r.status,
            containment=r.containment, child_distinct=r.child_distinct, parent_distinct=r.parent_distinct,
            parent_unique=r.parent_unique, relationship_id=str(r.relationship_id),
        )
        for r in rows
    ]


def _col_out(col) -> JoinColumnOut | None:
    return JoinColumnOut(field_id=uuid.UUID(col.field_id), table=col.entity_name, column=col.name) if col is not None else None


def _to_out(res: JoinResolution) -> JoinResolutionOut:
    return JoinResolutionOut(
        primitive=res.requirement.primitive,
        requires_left=res.requirement.left,
        requires_right=res.requirement.right,
        verdict=res.verdict,
        reason=res.reason,
        left=_col_out(res.left),
        right=_col_out(res.right),
        relationship=res.relationship,
        relationship_id=uuid.UUID(res.relationship_id) if res.relationship_id else None,
        ruling=res.ruling,
        containment=res.containment,
        evidence=list(res.evidence),
        alternatives=[JoinAlternativeOut(left=_col_out(a.left), right=_col_out(a.right), score=a.total) for a in res.alternatives],
    )


def build_join_report(
    db: Session,
    *,
    audit_test_id: uuid.UUID,
    rule_definition: dict | None = None,
    object_entities: dict[str, uuid.UUID] | None = None,
) -> JoinReportOut:
    control, definition = _rule_definition_for_test(db, audit_test_id)
    if rule_definition is not None:
        definition = rule_definition
    if not definition:
        return JoinReportOut(joins=[], paths=[])
    requirements = join_requirements_for(definition)
    if not requirements:
        return JoinReportOut(joins=[], paths=[])

    object_entity = object_entities if object_entities is not None else _object_entities(db, control, audit_test_id)
    bound_entities = list(set(object_entity.values()))
    columns = _load_columns(db, bound_entities)
    by_entity: dict[str, list[ColumnRef]] = {}
    for c in columns:
        by_entity.setdefault(c.entity_id, []).append(c)
    edges = _load_edges_for_sources(db, bound_entities)
    # Bridge tables' columns, so a path can be described through them.
    known = {c.field_id for c in columns}
    extra = {f for e in edges for f in (e.child_field_id, e.parent_field_id)} - known
    path_columns = columns + (_column_rows(db, DataField.field_id.in_([uuid.UUID(f) for f in extra])) if extra else [])

    results: list[JoinResolution] = []
    for req in requirements:
        left_entity, right_entity = object_entity.get(req.left_object), object_entity.get(req.right_object)
        if left_entity is None or right_entity is None:
            missing = req.left_object if left_entity is None else req.right_object
            results.append(
                JoinResolution(
                    requirement=req, verdict=UNRESOLVED,
                    reason=f"No table is bound for {missing} yet.", evidence=[f"Bind a table for {missing} first."],
                )
            )
            continue
        results.append(resolve_join(req, by_entity.get(str(left_entity), []), by_entity.get(str(right_entity), []), edges))

    paths = find_paths([str(e) for e in dict.fromkeys(object_entity.values())], path_columns, edges)
    return JoinReportOut(
        joins=[_to_out(r) for r in results],
        paths=[
            JoinPathOut(
                from_table=p.from_entity, to_table=p.to_entity, executed=p.executed,
                steps=[JoinPathStepOut(from_column=s.from_column, to_column=s.to_column) for s in p.steps],
            )
            for p in paths
        ],
        blocking=any(r.verdict == CONTRADICTED for r in results),
    )
