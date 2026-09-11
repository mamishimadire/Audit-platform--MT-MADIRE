import uuid
from typing import Literal

from app.schemas.common import OrmModel

ControlType = Literal["preventive", "detective", "corrective"]
ControlNature = Literal["manual", "automated", "it_dependent"]
ControlStatus = Literal["pending_mapping", "pending_activation", "active", "pending_deactivation", "inactive", "retired"]


class ControlActivateRequest(OrmModel):
    """Activates one control from the pre-built library — no manual entry."""

    control_library_id: uuid.UUID
    risk_ids: list[uuid.UUID] = []


class ControlDeactivationRequest(OrmModel):
    reason: str


class ControlRejectRequest(OrmModel):
    reason: str


class ControlOut(OrmModel):
    control_id: uuid.UUID
    organization_id: uuid.UUID
    control_library_id: uuid.UUID | None
    domain: str | None = None
    control_code: str | None
    control_name: str
    control_description: str | None
    control_type: str | None
    control_nature: str | None
    control_frequency: str | None
    control_owner_id: uuid.UUID | None
    status: str
    activation_requested_by: uuid.UUID | None
    activation_approved_by: uuid.UUID | None
    deactivation_requested_by: uuid.UUID | None
    deactivation_requested_reason: str | None
    deactivation_approved_by: uuid.UUID | None
    required_tables: list[str] = []
    risk_ids: list[uuid.UUID] = []
