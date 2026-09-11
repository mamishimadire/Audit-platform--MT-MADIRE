import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_test import AuditTest, ControlAuditTest
from app.models.risk_control import Control
from app.services.control_service import get_domain_and_tables


def list_audit_tests(db: Session, *, organization_id: uuid.UUID) -> list[AuditTest]:
    return list(db.scalars(select(AuditTest).where(AuditTest.organization_id == organization_id)))


def get_control_ids_for_test(db: Session, *, audit_test_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.scalars(select(ControlAuditTest.control_id).where(ControlAuditTest.audit_test_id == audit_test_id)))


def get_domain_and_tables_for_test(db: Session, *, control_ids: list[uuid.UUID]) -> tuple[str | None, list[str]]:
    """An auto-created test has exactly one linked control; a manually-built
    one (from before this became automatic) may have several or none — take
    the first one that resolves to a library entry."""
    for control_id in control_ids:
        control = db.get(Control, control_id)
        if control is not None and control.control_library_id is not None:
            return get_domain_and_tables(db, control_library_id=control.control_library_id)
    return None, []
