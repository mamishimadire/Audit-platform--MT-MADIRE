import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_test import AuditTest, ControlAuditTest
from app.models.control_library import ControlLibraryEntry
from app.models.risk_control import Control


def list_audit_tests(db: Session, *, organization_id: uuid.UUID) -> list[AuditTest]:
    return list(db.scalars(select(AuditTest).where(AuditTest.organization_id == organization_id)))


def describe_tests(
    db: Session, *, audit_test_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[list[uuid.UUID], str | None, list[str]]]:
    """3 queries total regardless of test count, instead of up to 3 per
    test (control links, then a per-control lookup, then a per-library-entry
    lookup). Each round trip to the database costs real, fixed latency
    (network + Neon), so an N-test list page doing 3N+1 queries turns a
    sub-second load into a multi-second one; this is the fix for exactly
    that. Returns audit_test_id -> (control_ids, domain, required_tables)."""
    if not audit_test_ids:
        return {}

    links = db.execute(
        select(ControlAuditTest.audit_test_id, ControlAuditTest.control_id).where(
            ControlAuditTest.audit_test_id.in_(audit_test_ids)
        )
    ).all()
    control_ids_by_test: dict[uuid.UUID, list[uuid.UUID]] = {}
    for test_id, control_id in links:
        control_ids_by_test.setdefault(test_id, []).append(control_id)

    all_control_ids = {control_id for _, control_id in links}
    controls = db.scalars(select(Control).where(Control.control_id.in_(all_control_ids))) if all_control_ids else []
    controls_by_id = {c.control_id: c for c in controls}

    library_ids = {c.control_library_id for c in controls_by_id.values() if c.control_library_id is not None}
    entries = db.scalars(select(ControlLibraryEntry).where(ControlLibraryEntry.control_library_id.in_(library_ids))) if library_ids else []
    entries_by_id = {e.control_library_id: e for e in entries}

    result: dict[uuid.UUID, tuple[list[uuid.UUID], str | None, list[str]]] = {}
    for test_id in audit_test_ids:
        control_ids = control_ids_by_test.get(test_id, [])
        domain, required_tables = None, []
        for control_id in control_ids:
            control = controls_by_id.get(control_id)
            entry = entries_by_id.get(control.control_library_id) if control else None
            if entry is not None:
                domain, required_tables = entry.domain, list(entry.required_tables)
                break
        result[test_id] = (control_ids, domain, required_tables)
    return result
