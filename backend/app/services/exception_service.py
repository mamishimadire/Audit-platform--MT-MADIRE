import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.evidence_exception import Exception_
from app.models.monitoring import TestExecution
from app.models.organization import OrganizationSetting
from app.services.audit_log_service import log_action

_CLOSING_STATUSES = ("resolved", "closed")
SOD_EXCEPTION_CLOSURE_SETTING = "sod_required_for_exception_closure"


def sod_required(db: Session, *, organization_id: uuid.UUID) -> bool:
    """
    Opt-in per organization via the existing organization_settings table
    (no new table needed) — defaults to off. A hard-coded global rule would
    immediately block a single person testing/demoing the whole platform
    solo (creating, owning, and closing their own test exception is exactly
    that workflow); clients who want the real control get to turn it on.

    Reused beyond exception closure itself — finding_service.create_retest
    checks the same flag to enforce remediation performer != verifier,
    since both are "does this org want a second person on closure-type
    actions" and a second, separate setting would just be two knobs
    controlling the same underlying policy question.
    """
    setting = db.scalar(
        select(OrganizationSetting).where(
            OrganizationSetting.organization_id == organization_id,
            OrganizationSetting.setting_name == SOD_EXCEPTION_CLOSURE_SETTING,
        )
    )
    return setting is not None and setting.setting_value == "true"

# An exception in any of these statuses is still "the same ongoing issue" —
# a fresh detection of it updates that row instead of inserting a new one.
# 'resolved'/'closed' are deliberately excluded: if the issue recurs after
# being marked fixed, that recurrence is a genuinely new exception, not a
# continuation — auto-merging it back in would let a real regression hide
# behind an old, already-closed record.
OPEN_STATUSES = ("open", "awaiting_evidence", "in_progress")


def find_open_exception(db: Session, *, audit_test_id: uuid.UUID, description: str) -> Exception_ | None:
    return db.scalar(
        select(Exception_)
        .join(TestExecution, TestExecution.execution_id == Exception_.execution_id)
        .where(
            TestExecution.audit_test_id == audit_test_id,
            Exception_.exception_description == description,
            Exception_.status.in_(OPEN_STATUSES),
        )
        .order_by(Exception_.last_detected_at.desc())
        .limit(1)
    )


def update_exception(
    db: Session, *, exception: Exception_, status: str | None, owner_id: uuid.UUID | None, organization_id: uuid.UUID,
    updated_by_user_id: uuid.UUID,
) -> Exception_:
    old_status = exception.status
    if (
        status in _CLOSING_STATUSES
        and exception.owner_id is not None
        and exception.owner_id == updated_by_user_id
        and sod_required(db, organization_id=organization_id)
    ):
        raise ValueError(
            "Segregation of duties: the assigned owner of this exception cannot also close it — "
            "have another authorized user review and close it instead."
        )
    if status is not None:
        exception.status = status
    if owner_id is not None:
        exception.owner_id = owner_id
    log_action(
        db,
        action=f"Exception status changed: {old_status} -> {exception.status}",
        organization_id=organization_id,
        user_id=updated_by_user_id,
        entity_type="exceptions",
        entity_id=exception.exception_id,
        old_value={"status": old_status},
        new_value={"status": exception.status, "owner_id": str(exception.owner_id) if exception.owner_id else None},
    )
    db.commit()
    db.refresh(exception)
    return exception
