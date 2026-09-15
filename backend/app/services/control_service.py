import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_test import AuditTest, ControlAuditTest
from app.models.control_library import ControlLibraryEntry
from app.models.risk_control import Control, Risk, RiskCategory, RiskControl
from app.schemas.control import ControlActivateRequest
from app.services.audit_log_service import log_action

# Every activated control gets exactly one matching audit test, auto-created
# in the same category/domain — the user does not hand-build a test to go
# with it (mirrors the "no manual control entry" rule for controls).
_AUTO_TEST_TYPE = "continuous_monitoring"


def activate_control(
    db: Session, *, organization_id: uuid.UUID, payload: ControlActivateRequest, created_by_user_id: uuid.UUID
) -> Control:
    """
    Creates an organization's `controls` row from a pre-built library entry —
    the only way controls are created now (no manual "Add a control" form).
    The organization still needs to confirm data source tables/field mappings
    (via the existing Audit Test + Data Mapping flow) before this control's
    testing is fully wired up, hence `status='pending_mapping'`.
    """
    library_entry = db.get(ControlLibraryEntry, payload.control_library_id)
    if library_entry is None:
        raise ValueError("Control library entry not found")

    existing = db.scalar(
        select(Control).where(
            Control.organization_id == organization_id,
            Control.control_library_id == library_entry.control_library_id,
        )
    )
    if existing is not None:
        raise ValueError(f"'{library_entry.control_code}' is already activated for this organization")

    control = Control(
        organization_id=organization_id,
        control_library_id=library_entry.control_library_id,
        control_code=library_entry.control_code,
        control_name=library_entry.control_name,
        control_description=library_entry.audit_procedure,
        control_type=library_entry.default_control_type,
        control_nature=library_entry.default_control_nature,
        control_frequency=library_entry.default_control_frequency,
        status="pending_mapping",
        created_by=created_by_user_id,
    )
    db.add(control)
    db.flush()

    linked_risk_ids: set[uuid.UUID] = set()
    for risk_id in payload.risk_ids:
        risk = db.get(Risk, risk_id)
        if risk is None or risk.organization_id != organization_id:
            raise ValueError(f"Risk {risk_id} does not exist in this organization")
        db.add(RiskControl(risk_id=risk_id, control_id=control.control_id))
        linked_risk_ids.add(risk_id)

    # Auto-link this control to every risk in the org's register that shares
    # its domain (the risk library and control library deliberately use the
    # same 20 domain names as risk category / control domain) — this is what
    # lets the Risks page show "mitigated" the moment a relevant control is
    # activated, without the user manually wiring risk-to-control by hand.
    same_domain_risk_ids = db.scalars(
        select(Risk.risk_id)
        .join(RiskCategory, RiskCategory.risk_category_id == Risk.risk_category_id)
        .where(Risk.organization_id == organization_id, RiskCategory.category_name == library_entry.domain)
    )
    for risk_id in same_domain_risk_ids:
        if risk_id in linked_risk_ids:
            continue
        db.add(RiskControl(risk_id=risk_id, control_id=control.control_id))
        linked_risk_ids.add(risk_id)

    test = AuditTest(
        organization_id=organization_id,
        test_code=library_entry.control_code,
        test_name=library_entry.control_name,
        test_description=library_entry.audit_procedure,
        test_type=_AUTO_TEST_TYPE,
        frequency=library_entry.default_control_frequency,
        status="draft",
        created_by=created_by_user_id,
    )
    db.add(test)
    db.flush()
    db.add(ControlAuditTest(control_id=control.control_id, audit_test_id=test.audit_test_id))

    log_action(
        db,
        action=f"Activated control '{control.control_code} — {control.control_name}' from the control library",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="controls",
        entity_id=control.control_id,
        new_value={"control_code": control.control_code, "control_library_id": str(library_entry.control_library_id)},
    )
    log_action(
        db,
        action=f"Auto-created audit test '{test.test_code} — {test.test_name}' for activated control",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="audit_tests",
        entity_id=test.audit_test_id,
        new_value={"test_code": test.test_code, "control_id": str(control.control_id)},
    )
    db.commit()
    db.refresh(control)
    return control


def request_activation(db: Session, *, control: Control, requested_by_user_id: uuid.UUID) -> Control:
    """Covers both a fresh activation (from pending_mapping) and reactivation
    (from inactive) — either way, someone else must approve it before the
    control actually starts governing anything (see approve_activation)."""
    if control.status not in ("pending_mapping", "inactive"):
        raise ValueError(f"Cannot request activation — control is '{control.status}'.")

    from app.services.control_binding_service import get_binding_progress

    progress = get_binding_progress(db, control=control)
    if not progress.ready:
        missing = [t for t in progress.required_tables if t not in {b.canonical_table_name for b in progress.bindings}]
        raise ValueError(
            f"Cannot request activation — {progress.satisfied} of {progress.total} required tables are bound. "
            f"Still missing: {', '.join(missing)}."
        )

    control.status = "pending_activation"
    control.activation_requested_by = requested_by_user_id
    control.activation_approved_by = None
    log_action(
        db,
        action=f"Requested activation of control '{control.control_code} — {control.control_name}'",
        organization_id=control.organization_id,
        user_id=requested_by_user_id,
        entity_type="controls",
        entity_id=control.control_id,
        new_value={"status": "pending_activation"},
    )
    db.commit()
    db.refresh(control)
    return control


def _linked_audit_tests(db: Session, *, control: Control) -> list[AuditTest]:
    return list(
        db.scalars(
            select(AuditTest)
            .join(ControlAuditTest, ControlAuditTest.audit_test_id == AuditTest.audit_test_id)
            .where(ControlAuditTest.control_id == control.control_id)
        )
    )


def approve_activation(db: Session, *, control: Control, approved_by_user_id: uuid.UUID) -> Control:
    if control.status != "pending_activation":
        raise ValueError(f"Cannot approve — control is '{control.status}', not pending activation.")
    if control.activation_requested_by is not None and control.activation_requested_by == approved_by_user_id:
        raise ValueError("You requested this activation yourself — a different authorized user must approve it.")

    control.status = "active"
    control.activation_approved_by = approved_by_user_id
    # The auto-created audit test was left at its creation-time 'draft'
    # status forever — nothing else in the codebase ever advanced it, so
    # the mapping screen kept showing "draft" even for a fully active,
    # executing control. It should track the control it belongs to.
    for test in _linked_audit_tests(db, control=control):
        test.status = "active"
    log_action(
        db,
        action=f"Approved activation of control '{control.control_code} — {control.control_name}'",
        organization_id=control.organization_id,
        user_id=approved_by_user_id,
        entity_type="controls",
        entity_id=control.control_id,
        new_value={"status": "active"},
    )
    db.commit()
    db.refresh(control)
    return control


def reject_activation(db: Session, *, control: Control, reason: str, rejected_by_user_id: uuid.UUID) -> Control:
    if control.status != "pending_activation":
        raise ValueError(f"Cannot reject — control is '{control.status}', not pending activation.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reject an activation request.")

    control.status = "pending_mapping" if control.activation_approved_by is None and control.deactivation_approved_by is None else "inactive"
    control.activation_requested_by = None
    log_action(
        db,
        action=f"Rejected activation of control '{control.control_code} — {control.control_name}': {reason}",
        organization_id=control.organization_id,
        user_id=rejected_by_user_id,
        entity_type="controls",
        entity_id=control.control_id,
        new_value={"status": control.status, "reason": reason},
    )
    db.commit()
    db.refresh(control)
    return control


def request_deactivation(db: Session, *, control: Control, reason: str, requested_by_user_id: uuid.UUID) -> Control:
    """Mandatory dual control, never skippable — the person who wants a
    monitoring control switched off is never the one who gets to make that
    happen unilaterally."""
    if control.status != "active":
        raise ValueError(f"Cannot request deactivation — control is '{control.status}', not active.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to request deactivation.")

    control.status = "pending_deactivation"
    control.deactivation_requested_by = requested_by_user_id
    control.deactivation_requested_reason = reason
    control.deactivation_approved_by = None
    log_action(
        db,
        action=f"Requested deactivation of control '{control.control_code} — {control.control_name}': {reason}",
        organization_id=control.organization_id,
        user_id=requested_by_user_id,
        entity_type="controls",
        entity_id=control.control_id,
        new_value={"status": "pending_deactivation", "reason": reason},
    )
    db.commit()
    db.refresh(control)
    return control


def approve_deactivation(db: Session, *, control: Control, approved_by_user_id: uuid.UUID) -> Control:
    if control.status != "pending_deactivation":
        raise ValueError(f"Cannot approve — control is '{control.status}', not pending deactivation.")
    if control.deactivation_requested_by is not None and control.deactivation_requested_by == approved_by_user_id:
        raise ValueError("You requested this deactivation yourself — a different authorized user must approve it.")

    control.status = "inactive"
    control.deactivation_approved_by = approved_by_user_id
    for test in _linked_audit_tests(db, control=control):
        test.status = "disabled"
    log_action(
        db,
        action=f"Approved deactivation of control '{control.control_code} — {control.control_name}'",
        organization_id=control.organization_id,
        user_id=approved_by_user_id,
        entity_type="controls",
        entity_id=control.control_id,
        new_value={"status": "inactive"},
    )
    db.commit()
    db.refresh(control)
    return control


def reject_deactivation(db: Session, *, control: Control, reason: str, rejected_by_user_id: uuid.UUID) -> Control:
    if control.status != "pending_deactivation":
        raise ValueError(f"Cannot reject — control is '{control.status}', not pending deactivation.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reject a deactivation request.")

    control.status = "active"
    control.deactivation_requested_by = None
    control.deactivation_requested_reason = None
    log_action(
        db,
        action=f"Rejected deactivation of control '{control.control_code} — {control.control_name}': {reason}",
        organization_id=control.organization_id,
        user_id=rejected_by_user_id,
        entity_type="controls",
        entity_id=control.control_id,
        new_value={"status": "active", "reason": reason},
    )
    db.commit()
    db.refresh(control)
    return control


def list_controls(db: Session, *, organization_id: uuid.UUID) -> list[Control]:
    return list(db.scalars(select(Control).where(Control.organization_id == organization_id)))


def get_risk_ids_for_control(db: Session, *, control_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.scalars(select(RiskControl.risk_id).where(RiskControl.control_id == control_id)))


def describe_controls(
    db: Session, *, controls: list[Control]
) -> dict[uuid.UUID, tuple[list[uuid.UUID], str | None, list[str]]]:
    """Batched equivalent of calling get_risk_ids_for_control +
    get_domain_and_tables once per control — 2 queries total regardless of
    how many controls, instead of up to 2 per control. Each round trip to
    the database costs real, fixed latency (network + Neon), so an N-control
    list page doing 2N queries turns a sub-second load into a multi-second
    one. Returns control_id -> (risk_ids, domain, required_tables)."""
    if not controls:
        return {}

    control_ids = [c.control_id for c in controls]
    risk_links = db.execute(select(RiskControl.control_id, RiskControl.risk_id).where(RiskControl.control_id.in_(control_ids)))
    risk_ids_by_control: dict[uuid.UUID, list[uuid.UUID]] = {}
    for control_id, risk_id in risk_links:
        risk_ids_by_control.setdefault(control_id, []).append(risk_id)

    library_ids = {c.control_library_id for c in controls if c.control_library_id is not None}
    entries = db.scalars(select(ControlLibraryEntry).where(ControlLibraryEntry.control_library_id.in_(library_ids))) if library_ids else []
    entries_by_id = {e.control_library_id: e for e in entries}

    result: dict[uuid.UUID, tuple[list[uuid.UUID], str | None, list[str]]] = {}
    for control in controls:
        entry = entries_by_id.get(control.control_library_id) if control.control_library_id else None
        domain, required_tables = (entry.domain, list(entry.required_tables)) if entry is not None else (None, [])
        result[control.control_id] = (risk_ids_by_control.get(control.control_id, []), domain, required_tables)
    return result


def get_domain_and_tables(db: Session, *, control_library_id: uuid.UUID | None) -> tuple[str | None, list[str]]:
    if control_library_id is None:
        return None, []
    entry = db.get(ControlLibraryEntry, control_library_id)
    if entry is None:
        return None, []
    return entry.domain, list(entry.required_tables)
