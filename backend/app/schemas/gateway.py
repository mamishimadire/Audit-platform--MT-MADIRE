import uuid
from datetime import datetime

from app.schemas.common import OrmModel


class GatewayCreate(OrmModel):
    gateway_name: str


class GatewayOut(OrmModel):
    gateway_id: uuid.UUID
    organization_id: uuid.UUID
    gateway_name: str
    registration_status: str
    status: str
    version: str | None
    last_heartbeat: datetime | None
    certificate_expiry: datetime | None


class GatewayCreatedOut(GatewayOut):
    """Returned only from the create call — carries the one-time registration code."""

    registration_code: str
    registration_code_expires_at: datetime


class GatewayRegisterRequest(OrmModel):
    registration_code: str
    gateway_name: str | None = None
    version: str | None = None


class GatewayRegisterResponse(OrmModel):
    gateway_id: uuid.UUID
    organization_id: uuid.UUID
    api_key: str  # shown exactly once


class GatewayHeartbeat(OrmModel):
    version: str | None = None
