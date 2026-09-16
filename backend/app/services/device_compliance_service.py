import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.execution_status import classify_completed_run
from app.models.audit_test import AuditTest
from app.models.device import Device, DeviceSoftwareInventory, DeviceTelemetry
from app.models.evidence_exception import Evidence, Exception_, ExceptionRecord
from app.models.monitoring import TestExecution
from app.schemas.device import ComplianceCheckDetail, TelemetryReport
from app.services.audit_log_service import log_action
from app.services.device_policy_service import get_device_policy
from app.services.exception_service import OPEN_STATUSES, find_open_exception
from app.services.software_compliance_service import check_software_compliance
from app.core.security import fingerprint

# Maps each check's telemetry field to the policy key that decides whether
# it's actually enforced for this organization — see device_policy_service.
_POLICY_KEY_BY_FIELD = {
    "antivirus_enabled": "require_antivirus",
    "firewall_enabled": "require_firewall",
    "disk_encryption_enabled": "require_disk_encryption",
    "os_up_to_date": "require_os_up_to_date",
}

COMPLIANCE_TEST_CODE = "EP-001"
COMPLIANCE_TEST_NAME = "Endpoint Security Compliance"

# (telemetry field, human description, severity, remediation guidance) —
# only an explicit False is ever flagged. An unknown/None value (e.g. the
# agent couldn't query it, or lacked permission) is never treated as a
# failure — that would be a false positive, not a real control breach.
_CHECKS: list[tuple[str, str, str, str]] = [
    (
        "antivirus_enabled",
        "Antivirus/real-time protection is disabled",
        "critical",
        "Open Windows Security → Virus & threat protection and turn on Real-time protection. If it's greyed out or "
        "won't stay on, check for third-party antivirus software that may have disabled Defender without providing "
        "its own active protection, and check Group Policy / Intune isn't forcing it off. Re-run the agent's check "
        "afterward to confirm the exception clears.",
    ),
    (
        "firewall_enabled",
        "Windows Firewall is disabled",
        "high",
        "Open Windows Security → Firewall & network protection and enable the firewall for every active network "
        "profile (Domain, Private, Public). If a third-party firewall is in use instead, confirm it's actually "
        "running and blocking unsolicited inbound connections, then have IT update the policy so this check reflects it.",
    ),
    (
        "disk_encryption_enabled",
        "Disk encryption (BitLocker) is not enabled",
        "high",
        "Open Settings → Privacy & security → Device encryption (or 'Manage BitLocker' on Pro/Enterprise editions) "
        "and turn on encryption for the system drive. Store the recovery key in your organization's key escrow "
        "(Entra ID/Active Directory), not locally — a device without an escrowed key is a separate finding on its own.",
    ),
    (
        "os_up_to_date",
        "Operating system is not up to date",
        "medium",
        "Open Settings → Windows Update and install all pending updates, then restart the device. If updates are "
        "managed centrally (WSUS/Intune), confirm this device is actually receiving and installing its assigned "
        "update ring rather than being stuck or excluded.",
    ),
]


# Positive framing for the checklist — _CHECKS above describes each check
# as its failure condition (what an exception's description reads), this is
# what to show when it's passing.
_PASS_LABELS = {
    "antivirus_enabled": "Antivirus active and running",
    "firewall_enabled": "Firewall enabled on all profiles",
    "disk_encryption_enabled": "Disk encryption (BitLocker) enabled",
    "os_up_to_date": "Operating system up to date",
}


def get_compliance_check_details(db: Session, *, device: Device) -> list[ComplianceCheckDetail]:
    """Per-check state for one device, 'since' timestamp included for any
    currently-failing check — sourced from the open EP-001 exception's own
    detected_at (first ever seen, never overwritten) and occurrence_count
    (bumped on every re-detection), not a separate history walk."""
    policy = get_device_policy(db, organization_id=device.organization_id)
    telemetry = db.scalar(
        select(DeviceTelemetry).where(DeviceTelemetry.device_id == device.device_id).order_by(DeviceTelemetry.collected_at.desc()).limit(1)
    )

    test = db.scalar(
        select(AuditTest).where(AuditTest.organization_id == device.organization_id, AuditTest.test_code == COMPLIANCE_TEST_CODE)
    )
    open_by_field: dict[str, Exception_] = {}
    if test is not None:
        prefix = f"{device.device_name}: "
        description_to_field = {desc: field for field, desc, _sev, _guidance in _CHECKS}
        open_rows = db.scalars(
            select(Exception_)
            .join(TestExecution, TestExecution.execution_id == Exception_.execution_id)
            .where(TestExecution.audit_test_id == test.audit_test_id, Exception_.status.in_(OPEN_STATUSES))
        )
        for row in open_rows:
            if not row.exception_description or not row.exception_description.startswith(prefix):
                continue
            field = description_to_field.get(row.exception_description[len(prefix):])
            if field is not None:
                open_by_field[field] = row

    details: list[ComplianceCheckDetail] = []
    for field, failure_desc, _sev, _guidance in _CHECKS:
        if not policy.get(_POLICY_KEY_BY_FIELD[field], True):
            continue  # disabled by org policy — not evaluated at all, don't show it either
        raw_value = getattr(telemetry, field) if telemetry is not None else None
        if raw_value is None:
            details.append(ComplianceCheckDetail(field=field, label=_PASS_LABELS[field], state="unknown"))
        elif field in open_by_field:
            exc = open_by_field[field]
            # Failing: show the failure description itself (what the exception
            # reads) as the title, not the positive label negated.
            details.append(
                ComplianceCheckDetail(
                    field=field, label=failure_desc, state="fail",
                    detected_at=exc.detected_at, occurrence_count=exc.occurrence_count,
                )
            )
        else:
            details.append(ComplianceCheckDetail(field=field, label=_PASS_LABELS[field], state="pass"))
    return details


def _get_or_create_compliance_test(db: Session, *, organization_id: uuid.UUID) -> AuditTest:
    test = db.scalar(
        select(AuditTest).where(AuditTest.organization_id == organization_id, AuditTest.test_code == COMPLIANCE_TEST_CODE)
    )
    if test is not None:
        return test
    test = AuditTest(
        organization_id=organization_id,
        test_code=COMPLIANCE_TEST_CODE,
        test_name=COMPLIANCE_TEST_NAME,
        test_description="Auto-created: evaluates endpoint security telemetry (AV, firewall, disk encryption, patch status) reported by the Madire Endpoint Agent.",
        test_type="endpoint_compliance",
        frequency="real_time",
        status="active",
    )
    db.add(test)
    db.flush()
    return test


def record_telemetry_and_check_compliance(db: Session, *, device: Device, report: TelemetryReport) -> TestExecution:
    """
    Unlike a database audit test (Gateway-executed, external data), device
    telemetry already lives in our own database the moment it's reported —
    so the compliance check runs immediately, server-side, and reuses the
    exact same audit_test -> test_execution -> exception -> finding chain
    a SQL-backed audit test would produce. A non-compliant device is not a
    special case; it is a normal exception like any other.
    """
    now = datetime.now(timezone.utc)

    device.last_heartbeat = now
    device.status = "online"
    if report.agent_version:
        device.agent_version = report.agent_version
    if report.hostname:
        device.hostname = report.hostname
    if report.os_name:
        device.os_name = report.os_name
    if report.os_version:
        device.os_version = report.os_version

    telemetry = DeviceTelemetry(
        device_id=device.device_id,
        antivirus_enabled=report.antivirus_enabled,
        firewall_enabled=report.firewall_enabled,
        disk_encryption_enabled=report.disk_encryption_enabled,
        os_up_to_date=report.os_up_to_date,
        raw_payload=report.raw_payload,
    )
    db.add(telemetry)
    db.flush()

    policy = get_device_policy(db, organization_id=device.organization_id)

    if report.installed_software is not None:
        inventory = db.get(DeviceSoftwareInventory, device.device_id)
        items = [item.model_dump() for item in report.installed_software]
        if inventory is None:
            db.add(DeviceSoftwareInventory(device_id=device.device_id, items=items, collected_at=now))
        else:
            inventory.items = items
            inventory.collected_at = now
        db.flush()
        # Same on/off semantics as the four checks below: disabling this
        # stops NEW software exceptions from being created, it doesn't
        # retroactively resolve exceptions already open.
        if policy.get("require_software_compliance", True):
            check_software_compliance(
                db, device=device, installed_software=report.installed_software, organization_id=device.organization_id
            )

    audit_test = _get_or_create_compliance_test(db, organization_id=device.organization_id)

    failed_checks = [
        (field, desc, sev, guidance)
        for field, desc, sev, guidance in _CHECKS
        if getattr(report, field) is False and policy.get(_POLICY_KEY_BY_FIELD[field], True)
    ]

    execution = TestExecution(
        audit_test_id=audit_test.audit_test_id,
        started_at=now,
        completed_at=now,
        # Same pass/exception vocabulary as every other execution path (see
        # app.core.execution_status) — this endpoint-side path used to write
        # its own "completed"/"failed" strings, which kept it permanently
        # invisible to the new-vocabulary filters/counts on the Executions
        # page and the dashboard's "Needs Attention" bucket.
        status=classify_completed_run(records_analyzed=1, exceptions_found=len(failed_checks)),
        records_analyzed=1,
    )
    db.add(execution)
    db.flush()

    execution.exceptions_found = len(failed_checks)

    evidence_summary = {
        "device_id": str(device.device_id),
        "hostname": device.hostname,
        "checks": {field: getattr(report, field) for field, _desc, _sev, _guidance in _CHECKS},
    }
    summary_json = json.dumps(evidence_summary, sort_keys=True)
    db.add(Evidence(execution_id=execution.execution_id, evidence_type="test_result", evidence_location=summary_json, evidence_hash=fingerprint(summary_json)))

    for field, description, severity, guidance in failed_checks:
        full_description = f"{device.device_name}: {description}"
        existing = find_open_exception(db, audit_test_id=audit_test.audit_test_id, description=full_description)

        if existing is not None:
            existing.last_detected_at = now
            existing.occurrence_count += 1
            exception_id = existing.exception_id
        else:
            exception_row = Exception_(
                execution_id=execution.execution_id,
                exception_description=full_description,
                recommended_remediation=guidance,
                severity=severity,
                status="open",
                last_detected_at=now,
            )
            db.add(exception_row)
            db.flush()
            exception_id = exception_row.exception_id

        db.add(
            ExceptionRecord(
                exception_id=exception_id,
                record_identifier=device.hostname or str(device.device_id),
                exception_data={"device_id": str(device.device_id), "hostname": device.hostname, "check": field, "value": False},
            )
        )

    # A check that now explicitly passes auto-resolves its own still-open
    # exception — re-testing a device's own compliance is exactly what a
    # 5-minute heartbeat is for, no auditor needs to notice and flip a
    # dropdown manually. This only closes the EXCEPTION; a Finding already
    # escalated from it keeps its own separate, governed closure workflow
    # (re-test/reviewer sign-off) rather than being silently auto-closed too.
    resolved_count = 0
    for field, description, _severity, _guidance in _CHECKS:
        if getattr(report, field) is not True:
            continue
        full_description = f"{device.device_name}: {description}"
        existing = find_open_exception(db, audit_test_id=audit_test.audit_test_id, description=full_description)
        if existing is None:
            continue
        existing.status = "resolved"
        existing.last_detected_at = now
        resolved_count += 1
        log_action(
            db,
            action=f"Exception auto-resolved: {full_description} (re-tested compliant)",
            organization_id=device.organization_id,
            entity_type="exceptions",
            entity_id=existing.exception_id,
            old_value={"status": "open"},
            new_value={"status": "resolved"},
        )

    log_action(
        db,
        action=f"Device telemetry processed: {len(failed_checks)} exception(s), {resolved_count} auto-resolved",
        organization_id=device.organization_id,
        entity_type="devices",
        entity_id=device.device_id,
        new_value={"exceptions_found": len(failed_checks), "auto_resolved": resolved_count},
    )

    db.commit()
    db.refresh(execution)
    return execution


def recheck_all_devices_from_latest_telemetry(db: Session, *, organization_id: uuid.UUID) -> None:
    """Evaluate the latest reported endpoint state after a policy approval.

    This intentionally does *not* call ``record_telemetry_and_check_compliance``:
    approving a policy is not a new agent heartbeat, so it must never make an
    offline device appear online or rewrite its last check-in timestamp.
    """
    devices = db.scalars(select(Device).where(Device.organization_id == organization_id, Device.status != "deleted")).all()
    policy = get_device_policy(db, organization_id=organization_id)
    audit_test = _get_or_create_compliance_test(db, organization_id=organization_id)

    for device in devices:
        telemetry = db.scalar(
            select(DeviceTelemetry)
            .where(DeviceTelemetry.device_id == device.device_id)
            .order_by(DeviceTelemetry.collected_at.desc())
            .limit(1)
        )
        if telemetry is None:
            continue  # unknown is not evidence of a compliance failure

        now = datetime.now(timezone.utc)
        failed_checks = [
            (field, description, severity, guidance)
            for field, description, severity, guidance in _CHECKS
            if getattr(telemetry, field) is False and policy.get(_POLICY_KEY_BY_FIELD[field], True)
        ]
        execution = TestExecution(
            audit_test_id=audit_test.audit_test_id,
            started_at=now,
            completed_at=now,
            status=classify_completed_run(records_analyzed=1, exceptions_found=len(failed_checks)),
            records_analyzed=1,
            exceptions_found=len(failed_checks),
        )
        db.add(execution)
        db.flush()
        summary_json = json.dumps(
            {
                "device_id": str(device.device_id),
                "hostname": device.hostname,
                "checks": {field: getattr(telemetry, field) for field, _desc, _sev, _guidance in _CHECKS},
                "evaluation_source": "policy_approval",
            },
            sort_keys=True,
        )
        db.add(
            Evidence(
                execution_id=execution.execution_id,
                evidence_type="test_result",
                evidence_location=summary_json,
                evidence_hash=fingerprint(summary_json),
            )
        )

        for field, description, severity, guidance in failed_checks:
            full_description = f"{device.device_name}: {description}"
            existing = find_open_exception(db, audit_test_id=audit_test.audit_test_id, description=full_description)
            if existing is not None:
                existing.last_detected_at = now
                existing.occurrence_count += 1
                exception_id = existing.exception_id
            else:
                exception_row = Exception_(
                    execution_id=execution.execution_id,
                    exception_description=full_description,
                    recommended_remediation=guidance,
                    severity=severity,
                    status="open",
                    last_detected_at=now,
                )
                db.add(exception_row)
                db.flush()
                exception_id = exception_row.exception_id
            db.add(
                ExceptionRecord(
                    exception_id=exception_id,
                    record_identifier=device.hostname or str(device.device_id),
                    exception_data={"device_id": str(device.device_id), "hostname": device.hostname, "check": field, "value": False},
                )
            )

        log_action(
            db,
            action=f"Device compliance re-evaluated after policy approval: {len(failed_checks)} exception(s)",
            organization_id=organization_id,
            entity_type="devices",
            entity_id=device.device_id,
            new_value={"exceptions_found": len(failed_checks), "source": "policy_approval"},
        )
        db.commit()
