import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_test import AuditTest
from app.models.monitoring import MonitoringSchedule
from app.schemas.audit_engine import MonitoringScheduleCreate
from app.services.audit_log_service import log_action

_FREQUENCY_DELTAS = {
    "real_time": timedelta(minutes=5),
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": timedelta(days=30),
}


def next_run_after(frequency: str, from_time: datetime) -> datetime:
    return from_time + _FREQUENCY_DELTAS.get(frequency, timedelta(days=1))


def create_schedule(
    db: Session, *, audit_test_id: uuid.UUID, payload: MonitoringScheduleCreate, organization_id: uuid.UUID, created_by_user_id: uuid.UUID
) -> MonitoringSchedule:
    """Every schedule starts pending a second person's approval — see
    approve_schedule. Nothing runs against real data until that happens,
    same reasoning as create_test_rule. Re-submitting the SAME frequency
    while that request is still pending returns the existing pending row
    instead of raising (double-click, retrying after a slow response); a
    genuinely different proposal while one is pending is rejected — only
    one draft can be awaiting approval per test at a time, backed by
    migration 0062's unique index as well.

    A test only ever has one LIVE (active) schedule. Submitting a new
    frequency while one is already active doesn't touch it — it creates a
    new pending version pointing back at the active one (supersedes_
    schedule_id), which only takes over once approved. This mirrors
    update_test_rule's edit-an-active-row shape exactly: what's actually
    running never changes on one person's say-so alone."""
    existing_pending = db.scalar(
        select(MonitoringSchedule).where(
            MonitoringSchedule.audit_test_id == audit_test_id, MonitoringSchedule.status == "pending_approval"
        )
    )
    if existing_pending is not None:
        if existing_pending.frequency == payload.frequency and existing_pending.is_active == payload.is_active:
            return existing_pending
        raise ValueError("A schedule change is already awaiting approval for this test.")

    existing_active = db.scalar(
        select(MonitoringSchedule).where(
            MonitoringSchedule.audit_test_id == audit_test_id, MonitoringSchedule.status == "active"
        )
    )
    if (
        existing_active is not None
        and existing_active.frequency == payload.frequency
        and existing_active.is_active == payload.is_active
    ):
        raise ValueError(f"'{payload.frequency}' is already the active schedule — nothing to request.")

    schedule = MonitoringSchedule(
        audit_test_id=audit_test_id,
        frequency=payload.frequency,
        next_run=None,  # not eligible to run until approved — see approve_schedule
        is_active=payload.is_active,
        status="pending_approval",
        created_by=created_by_user_id,
        version=(existing_active.version + 1) if existing_active else 1,
        supersedes_schedule_id=existing_active.schedule_id if existing_active else None,
    )
    db.add(schedule)
    db.flush()
    log_action(
        db,
        action="Requested monitoring schedule (pending approval)",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="monitoring_schedules",
        entity_id=schedule.schedule_id,
        new_value={"frequency": schedule.frequency, "audit_test_id": str(audit_test_id), "status": "pending_approval"},
    )
    db.commit()
    db.refresh(schedule)
    return schedule


def approve_schedule(
    db: Session, *, schedule: MonitoringSchedule, approved_by_user_id: uuid.UUID, organization_id: uuid.UUID
) -> MonitoringSchedule:
    """Maker-checker, identity-based — same pattern as test_rule_service.approve_rule.
    Whoever requested this schedule cannot also be the one who approves it."""
    if schedule.created_by is not None and schedule.created_by == approved_by_user_id:
        raise ValueError("You requested this schedule yourself — a different authorized user must approve it.")
    if schedule.status != "pending_approval":
        raise ValueError(f"This schedule is '{schedule.status}', not pending approval.")

    if schedule.supersedes_schedule_id is not None:
        previous = db.get(MonitoringSchedule, schedule.supersedes_schedule_id)
        if previous is not None and previous.status == "active":
            previous.status = "superseded"

    schedule.status = "active"
    schedule.approved_by = approved_by_user_id
    schedule.approved_at = datetime.now(timezone.utc)
    schedule.rejected_reason = None
    schedule.next_run = datetime.now(timezone.utc)  # eligible immediately; the first run establishes the cadence
    log_action(
        db,
        action="Approved monitoring schedule",
        organization_id=organization_id,
        user_id=approved_by_user_id,
        entity_type="monitoring_schedules",
        entity_id=schedule.schedule_id,
        new_value={"status": "active", "frequency": schedule.frequency},
    )
    db.commit()
    db.refresh(schedule)
    return schedule


def reject_schedule(
    db: Session, *, schedule: MonitoringSchedule, reason: str, rejected_by_user_id: uuid.UUID, organization_id: uuid.UUID
) -> MonitoringSchedule:
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reject a monitoring schedule.")
    if schedule.status != "pending_approval":
        raise ValueError(f"This schedule is '{schedule.status}', not pending approval.")

    schedule.status = "rejected"
    schedule.rejected_reason = reason.strip()
    log_action(
        db,
        action=f"Rejected monitoring schedule: {schedule.rejected_reason}",
        organization_id=organization_id,
        user_id=rejected_by_user_id,
        entity_type="monitoring_schedules",
        entity_id=schedule.schedule_id,
        new_value={"status": "rejected", "reason": schedule.rejected_reason},
    )
    db.commit()
    db.refresh(schedule)
    return schedule


def list_schedules(db: Session, *, audit_test_id: uuid.UUID) -> list[MonitoringSchedule]:
    return list(db.scalars(select(MonitoringSchedule).where(MonitoringSchedule.audit_test_id == audit_test_id)))


def list_schedules_for_organization(db: Session, *, organization_id: uuid.UUID) -> list[MonitoringSchedule]:
    return list(
        db.scalars(
            select(MonitoringSchedule)
            .join(AuditTest, AuditTest.audit_test_id == MonitoringSchedule.audit_test_id)
            .where(AuditTest.organization_id == organization_id)
        )
    )
