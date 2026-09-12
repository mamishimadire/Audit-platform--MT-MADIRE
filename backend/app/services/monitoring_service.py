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
    """Idempotent by design: a test only ever needs one active schedule.
    Re-submitting (double-click, re-opening the mapping panel, retrying
    after a slow response) updates the existing one instead of inserting a
    duplicate — see migration 0045, which also adds a DB-level unique index
    as a backstop in case two requests ever race past this check."""
    existing = db.scalar(
        select(MonitoringSchedule).where(
            MonitoringSchedule.audit_test_id == audit_test_id, MonitoringSchedule.is_active.is_(True)
        )
    )
    if existing is not None:
        if existing.frequency != payload.frequency:
            existing.frequency = payload.frequency
            existing.next_run = datetime.now(timezone.utc)
            db.commit()
            db.refresh(existing)
        return existing

    now = datetime.now(timezone.utc)
    schedule = MonitoringSchedule(
        audit_test_id=audit_test_id,
        frequency=payload.frequency,
        next_run=now,  # eligible immediately; the first run establishes the cadence
        is_active=payload.is_active,
    )
    db.add(schedule)
    db.flush()
    log_action(
        db,
        action="Created monitoring schedule",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="monitoring_schedules",
        entity_id=schedule.schedule_id,
        new_value={"frequency": schedule.frequency, "audit_test_id": str(audit_test_id)},
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
