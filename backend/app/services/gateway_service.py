import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import fingerprint, generate_gateway_api_key, generate_registration_code
from app.models.data_source import Gateway
from app.schemas.gateway import GatewayCreate
from app.services.audit_log_service import log_action

REGISTRATION_CODE_TTL_MINUTES = 15


def create_gateway(
    db: Session, *, organization_id: uuid.UUID, payload: GatewayCreate, created_by_user_id: uuid.UUID
) -> Gateway:
    gateway = Gateway(
        organization_id=organization_id,
        gateway_name=payload.gateway_name,
        registration_code=generate_registration_code(),
        registration_code_expires_at=datetime.now(timezone.utc) + timedelta(minutes=REGISTRATION_CODE_TTL_MINUTES),
        registration_status="unused",
        status="pending",
        created_by=created_by_user_id,
    )
    db.add(gateway)
    db.flush()
    log_action(
        db,
        action=f"Generated Gateway registration code for '{gateway.gateway_name}'",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="gateways",
        entity_id=gateway.gateway_id,
        new_value={"gateway_name": gateway.gateway_name},
    )
    db.commit()
    db.refresh(gateway)
    return gateway


def list_gateways(db: Session, *, organization_id: uuid.UUID) -> list[Gateway]:
    return list(db.scalars(select(Gateway).where(Gateway.organization_id == organization_id)))


def redeem_registration_code(
    db: Session, *, registration_code: str, gateway_name: str | None, version: str | None
) -> tuple[Gateway, str]:
    """
    Verifies the code exists, is unused and unexpired, then issues a device
    credential. Mirrors the product spec's lifecycle exactly: valid format
    is implicit (lookup just won't match), not expired, not previously used,
    belongs to the issuing tenant (organization_id already on the row) —
    and the code is invalidated the instant it's redeemed.
    """
    gateway = db.scalar(select(Gateway).where(Gateway.registration_code == registration_code))
    if gateway is None:
        raise ValueError("Invalid registration code")
    if gateway.registration_status != "unused":
        raise ValueError("Registration code has already been used or revoked")
    if gateway.registration_code_expires_at is None or gateway.registration_code_expires_at < datetime.now(timezone.utc):
        raise ValueError("Registration code has expired")

    api_key = generate_gateway_api_key()
    gateway.device_certificate_fingerprint = fingerprint(api_key)
    gateway.registration_status = "registered"
    gateway.status = "pending"  # becomes 'online' on first heartbeat
    gateway.registration_code = None  # the code plays no further role once redeemed
    gateway.registration_code_expires_at = None
    if gateway_name:
        gateway.gateway_name = gateway_name
    if version:
        gateway.version = version

    log_action(
        db,
        action=f"Gateway '{gateway.gateway_name}' registered",
        organization_id=gateway.organization_id,
        entity_type="gateways",
        entity_id=gateway.gateway_id,
        new_value={"registration_status": "registered"},
    )
    db.commit()
    db.refresh(gateway)
    return gateway, api_key


def authenticate_gateway(db: Session, *, gateway_id: uuid.UUID, api_key: str) -> Gateway | None:
    gateway = db.get(Gateway, gateway_id)
    if gateway is None or gateway.device_certificate_fingerprint is None:
        return None
    if gateway.registration_status != "registered" or gateway.status == "deregistered":
        return None
    if gateway.device_certificate_fingerprint != fingerprint(api_key):
        return None
    return gateway


def record_heartbeat(db: Session, *, gateway: Gateway, version: str | None) -> Gateway:
    gateway.last_heartbeat = datetime.now(timezone.utc)
    gateway.status = "online"
    if version:
        gateway.version = version
    db.commit()
    db.refresh(gateway)
    return gateway


def revoke_gateway(db: Session, *, gateway: Gateway, revoked_by_user_id: uuid.UUID) -> Gateway:
    gateway.status = "deregistered"
    gateway.registration_status = "revoked"
    log_action(
        db,
        action=f"Gateway '{gateway.gateway_name}' revoked",
        organization_id=gateway.organization_id,
        user_id=revoked_by_user_id,
        entity_type="gateways",
        entity_id=gateway.gateway_id,
    )
    db.commit()
    db.refresh(gateway)
    return gateway
