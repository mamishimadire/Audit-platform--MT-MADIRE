import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.risk_control import Risk, RiskCategory, RiskControl
from app.schemas.risk import RiskCreate, RiskUpdate
from app.services.audit_log_service import log_action


def create_risk(db: Session, *, organization_id: uuid.UUID, payload: RiskCreate, created_by_user_id: uuid.UUID) -> Risk:
    risk = Risk(
        organization_id=organization_id,
        process_id=payload.process_id,
        risk_category_id=payload.risk_category_id,
        risk_name=payload.risk_name,
        risk_description=payload.risk_description,
        inherent_risk_rating=payload.inherent_risk_rating,
        residual_risk_rating=payload.residual_risk_rating,
        risk_owner_id=payload.risk_owner_id,
        status="active",
        created_by=created_by_user_id,
    )
    db.add(risk)
    db.flush()
    log_action(
        db,
        action=f"Created risk '{risk.risk_name}'",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="risks",
        entity_id=risk.risk_id,
        new_value={"risk_name": risk.risk_name, "inherent_risk_rating": risk.inherent_risk_rating},
    )
    db.commit()
    db.refresh(risk)
    return risk


def list_risks(db: Session, *, organization_id: uuid.UUID) -> list[Risk]:
    return list(db.scalars(select(Risk).where(Risk.organization_id == organization_id)))


def update_risk(db: Session, *, risk: Risk, payload: RiskUpdate, updated_by_user_id: uuid.UUID) -> Risk:
    """Auto-created risks (from the risk library) stay fully editable — this
    is how a control-owner or auditor tailors the auto-generated register."""
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(risk, field, value)
    log_action(
        db,
        action=f"Updated risk '{risk.risk_name}'",
        organization_id=risk.organization_id,
        user_id=updated_by_user_id,
        entity_type="risks",
        entity_id=risk.risk_id,
        new_value=updates,
    )
    db.commit()
    db.refresh(risk)
    return risk


def list_risk_categories(db: Session) -> list[RiskCategory]:
    return list(db.scalars(select(RiskCategory).order_by(RiskCategory.category_name)))


def get_risk_ids_with_active_control(db: Session, *, organization_id: uuid.UUID) -> set[uuid.UUID]:
    stmt = (
        select(RiskControl.risk_id)
        .join(Risk, Risk.risk_id == RiskControl.risk_id)
        .where(Risk.organization_id == organization_id)
        .distinct()
    )
    return set(db.scalars(stmt))
