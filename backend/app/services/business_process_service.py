import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.business_process import BusinessProcess, ProcessActivity
from app.schemas.business_process import BusinessProcessCreate, ProcessActivityCreate
from app.services.audit_log_service import log_action


def create_business_process(
    db: Session, *, organization_id: uuid.UUID, payload: BusinessProcessCreate, created_by_user_id: uuid.UUID
) -> BusinessProcess:
    process = BusinessProcess(
        organization_id=organization_id,
        process_name=payload.process_name,
        process_code=payload.process_code,
        description=payload.description,
        process_owner_id=payload.process_owner_id,
        status="active",
        created_by=created_by_user_id,
    )
    db.add(process)
    db.flush()
    log_action(
        db,
        action=f"Created business process '{process.process_name}'",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="business_processes",
        entity_id=process.process_id,
        new_value={"process_name": process.process_name},
    )
    db.commit()
    db.refresh(process)
    return process


def list_business_processes(db: Session, *, organization_id: uuid.UUID) -> list[BusinessProcess]:
    return list(db.scalars(select(BusinessProcess).where(BusinessProcess.organization_id == organization_id)))


def create_process_activity(
    db: Session, *, process_id: uuid.UUID, payload: ProcessActivityCreate, created_by_user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> ProcessActivity:
    activity = ProcessActivity(
        process_id=process_id,
        activity_name=payload.activity_name,
        activity_description=payload.activity_description,
        activity_owner_id=payload.activity_owner_id,
        sequence_number=payload.sequence_number,
    )
    db.add(activity)
    db.flush()
    log_action(
        db,
        action=f"Created process activity '{activity.activity_name}'",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="process_activities",
        entity_id=activity.activity_id,
        new_value={"activity_name": activity.activity_name, "process_id": str(process_id)},
    )
    db.commit()
    db.refresh(activity)
    return activity


def list_process_activities(db: Session, *, process_id: uuid.UUID) -> list[ProcessActivity]:
    return list(
        db.scalars(
            select(ProcessActivity).where(ProcessActivity.process_id == process_id).order_by(ProcessActivity.sequence_number)
        )
    )
