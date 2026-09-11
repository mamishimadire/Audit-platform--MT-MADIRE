import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, uuid_pk


class Device(Base, TimestampMixin):
    __tablename__ = "devices"

    device_id: Mapped[uuid.UUID] = uuid_pk("device_id")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    device_name: Mapped[str] = mapped_column(String(150), nullable=False)
    assigned_user_name: Mapped[str | None] = mapped_column(String(150))
    hostname: Mapped[str | None] = mapped_column(String(150))
    os_name: Mapped[str | None] = mapped_column(String(100))
    os_version: Mapped[str | None] = mapped_column(String(100))
    registration_code: Mapped[str | None] = mapped_column(String(20))
    registration_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registration_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="unused")
    device_certificate_fingerprint: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    agent_version: Mapped[str | None] = mapped_column(String(20))
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    # Dual control on taking a device out of monitoring scope — see
    # migration 0033. requested_by != approved_by is enforced in
    # device_service, same identity check as control activation/deactivation.
    revocation_requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    revocation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    revocation_approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    revocation_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_reason: Mapped[str | None] = mapped_column(Text)
    deletion_approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    deletion_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceTelemetry(Base):
    __tablename__ = "device_telemetry"

    telemetry_id: Mapped[uuid.UUID] = uuid_pk("telemetry_id")
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False
    )
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    antivirus_enabled: Mapped[bool | None] = mapped_column(Boolean)
    firewall_enabled: Mapped[bool | None] = mapped_column(Boolean)
    disk_encryption_enabled: Mapped[bool | None] = mapped_column(Boolean)
    os_up_to_date: Mapped[bool | None] = mapped_column(Boolean)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)


class DevicePolicyChange(Base):
    """A proposed organization-wide device compliance policy.

    The live policy stays in ``organization_settings`` because that remains
    the efficient read shape for agents. Once a policy has an approval
    lifecycle, however, its proposed values and independent decision need a
    durable, auditable record of their own.
    """

    __tablename__ = "device_policy_changes"

    policy_change_id: Mapped[uuid.UUID] = uuid_pk("policy_change_id")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    proposed_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    approval_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending_approval")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[str | None] = mapped_column(Text)


class DeviceCommand(Base):
    """
    An admin-issued instruction the device's own agent picks up on its next
    poll and executes — never pushed/executed inline, since the agent only
    ever makes outbound calls (same no-inbound-ports principle as the
    Gateway). command_type is restricted to a fixed allowlist at the service
    layer, never arbitrary text a client could turn into remote code
    execution.
    """

    __tablename__ = "device_commands"

    command_id: Mapped[uuid.UUID] = uuid_pk("command_id")
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False
    )
    command_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_message: Mapped[str | None] = mapped_column(String(500))


class ApprovedSoftware(Base, TimestampMixin):
    """
    One organization's software policy (AS-004): a per-app classification,
    not just a yes/no allowlist. Matching is by app_name (+ optional
    publisher, to resist a lookalike name spoofing a classified one) — see
    software_compliance_service.py for how a device's reported inventory
    gets compared against this policy and for what each classification
    means for compliance (only 'restricted' present, or 'required' missing,
    ever creates an exception — 'unknown' does NOT mean non-compliant).
    """

    __tablename__ = "approved_software"

    approved_software_id: Mapped[uuid.UUID] = uuid_pk("approved_software_id")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    app_name: Mapped[str] = mapped_column(String(255), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(255))
    approved_version_min: Mapped[str | None] = mapped_column(String(50))
    category: Mapped[str | None] = mapped_column(String(100))
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, server_default="medium")
    # 'approved' preserves the exact meaning existing rows already had
    # before this column existed — no behavior change for anything already
    # configured. This is the classification VALUE — see approval_status
    # below for the separate maker-checker workflow state.
    classification: Mapped[str] = mapped_column(String(20), nullable=False, server_default="approved")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    # Maker-checker workflow — a classification only affects device
    # compliance once approval_status == 'approved' (see
    # software_compliance_service.classify_item). classified_by != approved_by,
    # enforced by user identity, same mechanism as every other workflow here.
    approval_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending_approval")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approved_software.approved_software_id", ondelete="SET NULL")
    )


class DeviceSoftwareInventory(Base):
    """
    Current-state snapshot, not history — one row per device, replaced on
    each report. Installed software rarely changes between the agent's
    5-minute heartbeats, so keeping every report as its own row (like
    DeviceTelemetry does for the compliance booleans) would just accumulate
    near-duplicate multi-KB payloads with no one asking for the history.
    """

    __tablename__ = "device_software_inventory"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True
    )
    items: Mapped[list] = mapped_column(JSONB, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
