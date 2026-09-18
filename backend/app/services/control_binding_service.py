"""
Enhancement 1: per-organization binding of a control's required canonical
tables to an actual discovered table, or an explicit reasoned
"not applicable." Gates activation — see control_service.set_control_status.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.canonical_model import TABLE_SUGGESTION_MIN_SCORE, score_table_name_match
from app.models.control_library import ControlLibraryEntry, ControlTableBinding
from app.models.data_source import DataEntity, DataSource
from app.models.risk_control import Control
from app.schemas.control_binding import (
    ControlTableBindingCreate,
    ControlTableNotApplicable,
    TableBindingProgressOut,
    TableBindingSuggestionOut,
)
from app.services.audit_log_service import log_action

# Enough to give an auditor a real choice without drowning a 130-collection
# data source's worth of near-misses under one required table.
_MAX_SUGGESTIONS_PER_TABLE = 3


def _org_entity_rows(db: Session, *, organization_id: uuid.UUID):
    """The organization-wide (entity, source_name) rows _suggest_bindings
    scores every unbound table against — split out so a caller scoring
    MANY controls in the same organization (see get_binding_progress_for_controls)
    can fetch this exactly once and reuse it, instead of once per control.

    is_hidden is a pure Data Sources page display declutter (see its
    docstring on the model) — a hidden table is still fully discovered
    and bindable everywhere else (the picker's own table list never
    filters on it either), so excluding it here too would make hiding a
    table silently break its control-mapping suggestions as a surprising
    side effect of an unrelated "tidy up my view" action."""
    return db.execute(
        select(DataEntity, DataSource.source_name)
        .join(DataSource, DataSource.data_source_id == DataEntity.data_source_id)
        .where(DataSource.organization_id == organization_id)
    ).all()


def _suggest_bindings(
    db: Session, *, organization_id: uuid.UUID, unbound_tables: list[str], rows=None
) -> dict[str, list[TableBindingSuggestionOut]]:
    """For each still-unmapped required table, rank every discovered table
    across the organization's data sources by name similarity — so the
    picker can default to a likely match instead of an auditor manually
    scanning every connection's discovered tables by hand (the exact pain
    point behind e.g. the 130-collection MongoDB demo data source)."""
    if not unbound_tables:
        return {}

    if rows is None:
        rows = _org_entity_rows(db, organization_id=organization_id)
    if not rows:
        return {}

    suggestions: dict[str, list[TableBindingSuggestionOut]] = {}
    for table in unbound_tables:
        scored = sorted(
            (
                (score_table_name_match(table, entity.entity_name), entity, source_name)
                for entity, source_name in rows
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        candidates = [
            TableBindingSuggestionOut(
                entity_id=entity.entity_id,
                entity_name=entity.entity_name,
                data_source_id=entity.data_source_id,
                source_name=source_name,
                confidence_score=score,
            )
            for score, entity, source_name in scored[:_MAX_SUGGESTIONS_PER_TABLE]
            if score >= TABLE_SUGGESTION_MIN_SCORE
        ]
        if candidates:
            suggestions[table] = candidates
    return suggestions


def _required_tables_for(db: Session, control: Control) -> list[str]:
    if control.control_library_id is None:
        return []
    entry = db.get(ControlLibraryEntry, control.control_library_id)
    return list(entry.required_tables) if entry else []


def _ensure_not_active(control: Control) -> None:
    """A live, active control's table bindings must not change silently —
    that's exactly the same class of unreviewed production change as
    editing an approved mapping or an active rule. Whoever wants to rebind
    a table on an active control must first request deactivation (which
    already requires a different person to approve it), make the change,
    then request reactivation — routing it through the dual-control gate
    that already exists rather than inventing a second, parallel one."""
    if control.status == "active":
        raise ValueError(
            "This control is active — request deactivation first to change its table bindings, "
            "then reactivate once the change is made."
        )


def list_bindings(db: Session, *, control_id: uuid.UUID) -> list[ControlTableBinding]:
    return list(db.scalars(select(ControlTableBinding).where(ControlTableBinding.control_id == control_id)))


def get_binding_progress(db: Session, *, control: Control) -> TableBindingProgressOut:
    required = _required_tables_for(db, control)
    bindings = list_bindings(db, control_id=control.control_id)
    by_table = {b.canonical_table_name: b for b in bindings}

    out_bindings = []
    for b in bindings:
        entity = db.get(DataEntity, b.entity_id) if b.entity_id else None
        source = db.get(DataSource, b.data_source_id) if b.data_source_id else None
        out_bindings.append(
            {
                "binding_id": b.binding_id,
                "organization_id": b.organization_id,
                "control_id": b.control_id,
                "canonical_table_name": b.canonical_table_name,
                "data_source_id": b.data_source_id,
                "entity_id": b.entity_id,
                "entity_name": entity.entity_name if entity else None,
                "source_name": source.source_name if source else None,
                "status": b.status,
                "not_applicable_reason": b.not_applicable_reason,
                "bound_by": b.bound_by,
                "bound_at": b.bound_at,
            }
        )

    satisfied = sum(1 for t in required if t in by_table)
    unbound = [t for t in required if t not in by_table]
    suggestions = _suggest_bindings(db, organization_id=control.organization_id, unbound_tables=unbound)
    return TableBindingProgressOut(
        required_tables=required,
        bindings=out_bindings,
        total=len(required),
        satisfied=satisfied,
        ready=satisfied == len(required),
        suggestions=suggestions,
    )


def get_binding_progress_for_controls(
    db: Session, *, controls: list[Control]
) -> dict[uuid.UUID, TableBindingProgressOut]:
    """Batched equivalent of calling get_binding_progress once per control —
    a handful of queries total regardless of how many controls, instead of
    up to (1 + 2 * bindings) per control (see describe_controls's own
    docstring for why this matters). The Controls page used to call
    get_binding_progress once per activated control, in parallel, on every
    page load AND after every single activation — fine at a handful of
    controls, but an organization that activates most of the library (100+
    controls) turned every one of those into 100+ simultaneous round trips,
    each doing its own handful of queries, and it got worse with every
    additional control activated. Returns control_id -> TableBindingProgressOut."""
    if not controls:
        return {}

    control_ids = [c.control_id for c in controls]
    library_ids = {c.control_library_id for c in controls if c.control_library_id is not None}
    entries = (
        {e.control_library_id: e for e in db.scalars(select(ControlLibraryEntry).where(ControlLibraryEntry.control_library_id.in_(library_ids)))}
        if library_ids
        else {}
    )

    bindings_by_control: dict[uuid.UUID, list[ControlTableBinding]] = {}
    for b in db.scalars(select(ControlTableBinding).where(ControlTableBinding.control_id.in_(control_ids))):
        bindings_by_control.setdefault(b.control_id, []).append(b)

    entity_ids = {b.entity_id for bindings in bindings_by_control.values() for b in bindings if b.entity_id}
    source_ids = {b.data_source_id for bindings in bindings_by_control.values() for b in bindings if b.data_source_id}
    entities = {e.entity_id: e for e in db.scalars(select(DataEntity).where(DataEntity.entity_id.in_(entity_ids)))} if entity_ids else {}
    sources = {s.data_source_id: s for s in db.scalars(select(DataSource).where(DataSource.data_source_id.in_(source_ids)))} if source_ids else {}

    # Every control here belongs to the same organization (the Controls
    # page only ever asks for its own org's controls) — this org-wide rows
    # fetch is the one _suggest_bindings would otherwise repeat, unchanged,
    # once per control.
    org_rows = _org_entity_rows(db, organization_id=controls[0].organization_id) if controls else []

    result: dict[uuid.UUID, TableBindingProgressOut] = {}
    for control in controls:
        entry = entries.get(control.control_library_id) if control.control_library_id else None
        required = list(entry.required_tables) if entry else []
        control_bindings = bindings_by_control.get(control.control_id, [])
        by_table = {b.canonical_table_name: b for b in control_bindings}

        out_bindings = []
        for b in control_bindings:
            entity = entities.get(b.entity_id) if b.entity_id else None
            source = sources.get(b.data_source_id) if b.data_source_id else None
            out_bindings.append(
                {
                    "binding_id": b.binding_id,
                    "organization_id": b.organization_id,
                    "control_id": b.control_id,
                    "canonical_table_name": b.canonical_table_name,
                    "data_source_id": b.data_source_id,
                    "entity_id": b.entity_id,
                    "entity_name": entity.entity_name if entity else None,
                    "source_name": source.source_name if source else None,
                    "status": b.status,
                    "not_applicable_reason": b.not_applicable_reason,
                    "bound_by": b.bound_by,
                    "bound_at": b.bound_at,
                }
            )

        satisfied = sum(1 for t in required if t in by_table)
        unbound = [t for t in required if t not in by_table]
        suggestions = _suggest_bindings(db, organization_id=control.organization_id, unbound_tables=unbound, rows=org_rows)
        result[control.control_id] = TableBindingProgressOut(
            required_tables=required,
            bindings=out_bindings,
            total=len(required),
            satisfied=satisfied,
            ready=satisfied == len(required),
            suggestions=suggestions,
        )
    return result


def is_fully_bound(db: Session, *, control: Control) -> bool:
    return get_binding_progress(db, control=control).ready


def _existing_binding(db: Session, *, control_id: uuid.UUID, canonical_table_name: str) -> ControlTableBinding | None:
    return db.scalar(
        select(ControlTableBinding).where(
            ControlTableBinding.control_id == control_id,
            ControlTableBinding.canonical_table_name == canonical_table_name,
        )
    )


def bind_table(
    db: Session, *, control: Control, payload: ControlTableBindingCreate, bound_by_user_id: uuid.UUID
) -> ControlTableBinding:
    _ensure_not_active(control)
    required = _required_tables_for(db, control)
    if payload.canonical_table_name not in required:
        raise ValueError(f"'{payload.canonical_table_name}' is not a required table for this control")

    # Rebinding (e.g. correcting a wrong auto-suggested table) updates the
    # existing row in place rather than delete-and-recreate — the same
    # discovery-ID-stability principle applies here: destroying and
    # remaking the row would lose its original bound_at/bound_by history
    # for no reason, since nothing about "this control's binding for this
    # table" is actually a new fact just because the target changed.
    existing = _existing_binding(db, control_id=control.control_id, canonical_table_name=payload.canonical_table_name)
    old_value = None
    if existing is not None:
        old_value = {
            "status": existing.status,
            "data_source_id": str(existing.data_source_id) if existing.data_source_id else None,
            "entity_id": str(existing.entity_id) if existing.entity_id else None,
        }
        existing.data_source_id = payload.data_source_id
        existing.entity_id = payload.entity_id
        existing.status = "bound"
        existing.not_applicable_reason = None
        existing.bound_by = bound_by_user_id
        binding = existing
    else:
        binding = ControlTableBinding(
            organization_id=control.organization_id,
            control_id=control.control_id,
            canonical_table_name=payload.canonical_table_name,
            data_source_id=payload.data_source_id,
            entity_id=payload.entity_id,
            status="bound",
            bound_by=bound_by_user_id,
        )
        db.add(binding)
    db.flush()
    log_action(
        db,
        action=f"Bound required table '{payload.canonical_table_name}' for control '{control.control_code}'",
        organization_id=control.organization_id,
        user_id=bound_by_user_id,
        entity_type="control_table_bindings",
        entity_id=binding.binding_id,
        old_value=old_value,
        new_value={"canonical_table_name": payload.canonical_table_name, "entity_id": str(payload.entity_id)},
    )
    db.commit()
    db.refresh(binding)
    return binding


def mark_not_applicable(
    db: Session, *, control: Control, payload: ControlTableNotApplicable, bound_by_user_id: uuid.UUID
) -> ControlTableBinding:
    _ensure_not_active(control)
    required = _required_tables_for(db, control)
    if payload.canonical_table_name not in required:
        raise ValueError(f"'{payload.canonical_table_name}' is not a required table for this control")
    if not payload.reason or not payload.reason.strip():
        raise ValueError("A reason is required to mark a table not applicable")

    existing = _existing_binding(db, control_id=control.control_id, canonical_table_name=payload.canonical_table_name)
    old_value = None
    if existing is not None:
        old_value = {
            "status": existing.status,
            "data_source_id": str(existing.data_source_id) if existing.data_source_id else None,
            "entity_id": str(existing.entity_id) if existing.entity_id else None,
        }
        existing.data_source_id = None
        existing.entity_id = None
        existing.status = "not_applicable"
        existing.not_applicable_reason = payload.reason
        existing.bound_by = bound_by_user_id
        binding = existing
    else:
        binding = ControlTableBinding(
            organization_id=control.organization_id,
            control_id=control.control_id,
            canonical_table_name=payload.canonical_table_name,
            status="not_applicable",
            not_applicable_reason=payload.reason,
            bound_by=bound_by_user_id,
        )
        db.add(binding)
    db.flush()
    log_action(
        db,
        action=f"Marked '{payload.canonical_table_name}' not applicable for control '{control.control_code}': {payload.reason}",
        organization_id=control.organization_id,
        user_id=bound_by_user_id,
        entity_type="control_table_bindings",
        entity_id=binding.binding_id,
        old_value=old_value,
        new_value={"canonical_table_name": payload.canonical_table_name, "status": "not_applicable", "reason": payload.reason},
    )
    db.commit()
    db.refresh(binding)
    return binding


def unbind_table(db: Session, *, control: Control, canonical_table_name: str, unbound_by_user_id: uuid.UUID) -> None:
    """Clears a mistaken bind/not-applicable entirely, back to unmapped —
    unlike bind_table/mark_not_applicable, there's nothing to preserve
    continuity of here since the auditor is saying this binding never
    should have existed, not correcting which table it points to."""
    _ensure_not_active(control)
    existing = _existing_binding(db, control_id=control.control_id, canonical_table_name=canonical_table_name)
    if existing is None:
        raise ValueError(f"'{canonical_table_name}' is not currently bound for this control")

    old_value = {
        "status": existing.status,
        "data_source_id": str(existing.data_source_id) if existing.data_source_id else None,
        "entity_id": str(existing.entity_id) if existing.entity_id else None,
    }
    binding_id = existing.binding_id
    db.delete(existing)
    log_action(
        db,
        action=f"Unbound '{canonical_table_name}' for control '{control.control_code}'",
        organization_id=control.organization_id,
        user_id=unbound_by_user_id,
        entity_type="control_table_bindings",
        entity_id=binding_id,
        old_value=old_value,
        new_value={"canonical_table_name": canonical_table_name, "status": "unmapped"},
    )
    db.commit()
