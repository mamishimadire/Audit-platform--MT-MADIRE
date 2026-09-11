import uuid
from datetime import datetime
from typing import Any, Literal

from app.schemas.common import OrmModel

ComplianceStatus = Literal["compliant", "non_compliant", "unknown"]


class DeviceCreate(OrmModel):
    device_name: str
    assigned_user_name: str | None = None


class DeviceOut(OrmModel):
    device_id: uuid.UUID
    organization_id: uuid.UUID
    device_name: str
    assigned_user_name: str | None
    hostname: str | None
    os_name: str | None
    os_version: str | None
    registration_status: str
    status: str
    agent_version: str | None
    last_heartbeat: datetime | None
    # Latest telemetry snapshot + a rolled-up verdict, so the fleet table
    # doesn't need a second round trip per device to show a security column.
    antivirus_enabled: bool | None = None
    firewall_enabled: bool | None = None
    disk_encryption_enabled: bool | None = None
    os_up_to_date: bool | None = None
    compliance_status: ComplianceStatus = "unknown"
    revocation_requested_by: uuid.UUID | None = None
    revocation_requested_at: datetime | None = None
    revocation_reason: str | None = None
    revocation_approved_by: uuid.UUID | None = None
    revocation_approved_at: datetime | None = None
    deletion_requested_by: uuid.UUID | None = None
    deletion_requested_at: datetime | None = None
    deletion_reason: str | None = None
    deletion_approved_by: uuid.UUID | None = None
    deletion_approved_at: datetime | None = None


class DeviceCreatedOut(DeviceOut):
    registration_code: str
    registration_code_expires_at: datetime


class DeviceLifecycleRequest(OrmModel):
    reason: str


class DeviceRegisterRequest(OrmModel):
    registration_code: str
    device_name: str | None = None
    hostname: str | None = None
    os_name: str | None = None
    os_version: str | None = None
    agent_version: str | None = None


class DeviceRegisterResponse(OrmModel):
    device_id: uuid.UUID
    organization_id: uuid.UUID
    api_key: str  # shown exactly once


class InstalledSoftwareItem(OrmModel):
    name: str
    version: str | None = None
    publisher: str | None = None


class TelemetryReport(OrmModel):
    agent_version: str | None = None
    hostname: str | None = None
    os_name: str | None = None
    os_version: str | None = None
    antivirus_enabled: bool | None = None
    firewall_enabled: bool | None = None
    disk_encryption_enabled: bool | None = None
    os_up_to_date: bool | None = None
    installed_software: list[InstalledSoftwareItem] | None = None
    raw_payload: dict[str, Any] | None = None


class DeviceTelemetryOut(OrmModel):
    telemetry_id: uuid.UUID
    device_id: uuid.UUID
    collected_at: datetime
    antivirus_enabled: bool | None
    firewall_enabled: bool | None
    disk_encryption_enabled: bool | None
    os_up_to_date: bool | None


SoftwareClassification = Literal["approved", "required", "restricted", "system_component", "ignored", "review_required", "unknown"]
# What a classification means for THIS device's compliance right now — only
# 'non_compliant' (restricted, present) and 'outdated' (below approved_version_min)
# ever create an audit exception. 'unknown' is deliberately not an exception:
# an unclassified app is not evidence of anything, just something nobody has
# looked at yet.
SoftwareComplianceResult = Literal["compliant", "non_compliant", "outdated", "review_required", "not_evaluated"]


class EnrichedSoftwareItem(InstalledSoftwareItem):
    classification: SoftwareClassification
    compliance_result: SoftwareComplianceResult


class DeviceSoftwareOut(OrmModel):
    device_id: uuid.UUID
    collected_at: datetime
    items: list[EnrichedSoftwareItem]


CommandType = Literal["run_check_now", "restart"]
CommandStatus = Literal["pending", "sent", "completed", "failed"]


class DevicePolicyOut(OrmModel):
    require_antivirus: bool
    require_firewall: bool
    require_disk_encryption: bool
    require_os_up_to_date: bool
    require_software_compliance: bool


PolicyApprovalStatus = Literal["pending_approval", "approved", "rejected"]


class DevicePolicyChangeOut(OrmModel):
    policy_change_id: uuid.UUID
    organization_id: uuid.UUID
    proposed_policy: DevicePolicyOut
    approval_status: PolicyApprovalStatus
    requested_by: uuid.UUID | None = None
    requested_at: datetime
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    rejected_by: uuid.UUID | None = None
    rejected_at: datetime | None = None
    rejected_reason: str | None = None


class DevicePolicyRejectRequest(OrmModel):
    reason: str


ComplianceCheckState = Literal["pass", "fail", "unknown"]


class ComplianceCheckDetail(OrmModel):
    """One compliance check's current state for one device, with 'since'
    tracking sourced from the open exception's own detected_at/
    occurrence_count — no separate history table needed, since
    device_compliance_service already updates those two fields in place on
    every re-detection instead of inserting a new exception row each time."""

    field: str
    label: str
    state: ComplianceCheckState
    detected_at: datetime | None = None
    occurrence_count: int | None = None


class DeviceCommandCreate(OrmModel):
    command_type: CommandType


class DeviceCommandOut(OrmModel):
    command_id: uuid.UUID
    device_id: uuid.UUID
    command_type: str
    status: str
    requested_at: datetime
    completed_at: datetime | None
    result_message: str | None


class PendingCommandOut(OrmModel):
    command_id: uuid.UUID
    command_type: str


class DeviceInstructions(OrmModel):
    """What the agent receives on each poll: the org's current compliance
    policy (Layer 3) and any commands awaiting execution (Layer 2)."""

    policy: DevicePolicyOut
    pending_commands: list[PendingCommandOut]


class CommandResultReport(OrmModel):
    success: bool
    message: str | None = None


RiskLevel = Literal["low", "medium", "high", "critical"]


# A policy row's own classification — distinct from SoftwareClassification
# above only in that 'unknown' isn't a settable policy, it's the fallback
# for software with no policy row at all.
PolicyClassification = Literal["approved", "required", "restricted", "system_component", "ignored", "review_required"]


class ApprovedSoftwareCreate(OrmModel):
    app_name: str
    publisher: str | None = None
    approved_version_min: str | None = None
    category: str | None = None
    risk_level: RiskLevel = "medium"
    classification: PolicyClassification = "approved"


ApprovalStatus = Literal["pending_approval", "approved", "rejected", "superseded"]


class ApprovedSoftwareOut(OrmModel):
    approved_software_id: uuid.UUID
    organization_id: uuid.UUID
    app_name: str
    publisher: str | None
    approved_version_min: str | None
    category: str | None
    risk_level: str
    classification: str
    approval_status: ApprovalStatus
    created_by: uuid.UUID | None = None
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    rejected_by: uuid.UUID | None = None
    rejected_at: datetime | None = None
    rejected_reason: str | None = None
    version: int = 1
    supersedes_id: uuid.UUID | None = None


class ApprovedSoftwareRejectRequest(OrmModel):
    reason: str
