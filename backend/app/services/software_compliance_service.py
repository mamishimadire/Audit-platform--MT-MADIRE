"""
Turns a device's raw software inventory into a real compliance test
(AS-004) using a classification taxonomy, not a binary allowlist:
approved, required, restricted, system_component, ignored, review_required
— plus 'unknown' for anything with no matching policy row at all.

Critically: 'unknown' does NOT mean non-compliant. An app nobody has
classified yet is not evidence of anything — it's flagged for visibility
(compliance_result='review_required') but never creates an audit exception.
Only two things ever create an exception: a 'restricted' app being present,
or a 'required' app being absent (or an 'approved'/'required' app below its
minimum version). Everything else (approved present, system_component,
ignored, review_required, unknown) is compliance-neutral.

Reuses the exact same audit_test -> test_execution -> exception chain as
device_compliance_service.py.
"""
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.execution_status import classify_completed_run
from app.models.audit_test import AuditTest
from app.models.device import ApprovedSoftware, Device, DeviceSoftwareInventory
from app.models.evidence_exception import Evidence, Exception_, ExceptionRecord
from app.models.monitoring import TestExecution
from app.schemas.device import ApprovedSoftwareCreate, EnrichedSoftwareItem, InstalledSoftwareItem
from app.services.audit_log_service import log_action
from app.services.exception_service import OPEN_STATUSES, find_open_exception
from app.core.security import fingerprint

SOFTWARE_TEST_CODE = "AS-004"
SOFTWARE_TEST_NAME = "Software Compliance (Approved / Required / Restricted)"

_RISK_SEVERITY = {"low": "low", "medium": "medium", "high": "high", "critical": "critical"}

# Only these compliance_result values ever produce an audit exception —
# everything else (compliant, review_required, not_evaluated) is
# deliberately silent. See module docstring for why 'unknown' isn't here.
_EXCEPTION_RESULTS = {"non_compliant", "outdated"}


def list_approved_software(db: Session, *, organization_id: uuid.UUID) -> list[ApprovedSoftware]:
    """Everything, including pending/rejected/superseded rows — the person
    reviewing a classification needs to see it before it's approved, and
    the person who submitted it needs to see it if it's rejected. Compliance
    checking uses _list_approved_software_in_effect instead, not this."""
    return list(
        db.scalars(
            select(ApprovedSoftware)
            .where(ApprovedSoftware.organization_id == organization_id)
            .order_by(ApprovedSoftware.app_name, ApprovedSoftware.version)
        )
    )


def _list_approved_software_in_effect(db: Session, *, organization_id: uuid.UUID) -> list[ApprovedSoftware]:
    """Only approval_status == 'approved' rows ever affect device
    compliance — a classification just submitted (or rejected) must read as
    'unknown / review required' on every device, exactly like no policy row
    existed at all, until an independent reviewer approves it."""
    return list(
        db.scalars(
            select(ApprovedSoftware).where(
                ApprovedSoftware.organization_id == organization_id, ApprovedSoftware.approval_status == "approved"
            )
        )
    )


def recheck_all_devices_for_org(db: Session, *, organization_id: uuid.UUID) -> None:
    """
    Classifying or reclassifying an app changes what every device's
    ALREADY-REPORTED inventory means for compliance right now — the
    enriched view (enrich_installed_software) already reflects this live,
    but the actual Exception rows behind the dashboard's counts only used
    to update on the device's NEXT heartbeat. That gap meant re-tagging
    something "Restricted" made the Devices page show "Non-compliant"
    immediately while Open Exceptions stayed unchanged until the agent next
    checked in — sometimes minutes away, sometimes never for a demo/offline
    device. Re-running the check against each device's stored inventory
    right away closes that gap.
    """
    from app.services.device_policy_service import get_device_policy  # deferred: avoids a service-to-service import cycle

    if not get_device_policy(db, organization_id=organization_id).get("require_software_compliance", True):
        return

    # .all() up front, not iterated lazily — check_software_compliance
    # commits inside the loop, and committing mid-fetch against the same
    # session/connection that's still streaming this query's results can
    # silently truncate the iteration after the first commit.
    devices = db.scalars(select(Device).where(Device.organization_id == organization_id, Device.status != "deleted")).all()
    for device in devices:
        inventory = db.get(DeviceSoftwareInventory, device.device_id)
        if inventory is None:
            continue
        items = [InstalledSoftwareItem.model_validate(item) for item in inventory.items]
        check_software_compliance(db, device=device, installed_software=items, organization_id=organization_id)


def create_approved_software(
    db: Session, *, organization_id: uuid.UUID, payload: ApprovedSoftwareCreate, created_by_user_id: uuid.UUID
) -> ApprovedSoftware:
    """Classifying an app no longer takes effect on its own — it starts
    'pending_approval' and reads as unknown/review-required on every device
    until a different, authorized user approves it (see approve_classification)."""
    entry = ApprovedSoftware(
        organization_id=organization_id,
        app_name=payload.app_name,
        publisher=payload.publisher,
        approved_version_min=payload.approved_version_min,
        category=payload.category,
        risk_level=payload.risk_level,
        classification=payload.classification,
        created_by=created_by_user_id,
        approval_status="pending_approval",
    )
    db.add(entry)
    db.flush()
    log_action(
        db,
        action=f"Classified '{entry.app_name}' as {entry.classification} — pending approval",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="approved_software",
        entity_id=entry.approved_software_id,
        new_value={"app_name": entry.app_name, "publisher": entry.publisher, "classification": entry.classification, "risk_level": entry.risk_level},
    )
    db.commit()
    db.refresh(entry)
    return entry


def update_approved_software(
    db: Session, *, entry: ApprovedSoftware, payload: ApprovedSoftwareCreate, updated_by_user_id: uuid.UUID
) -> ApprovedSoftware:
    """Editing a not-yet-approved (or rejected) classification just updates
    it in place — nothing live depends on it yet. Editing an ALREADY-APPROVED
    classification instead creates a new pending_approval row (version+1,
    supersedes_id pointing back) and leaves the old row untouched and still
    enforcing — exactly like editing an active test rule. A reclassification
    someone flags as low-risk (Unknown -> Approved) never silently becomes
    the live control any more than a high-risk one (Approved -> Restricted)
    does; both go through the same review."""
    if entry.approval_status == "approved":
        new_entry = ApprovedSoftware(
            organization_id=entry.organization_id,
            app_name=payload.app_name,
            publisher=payload.publisher,
            approved_version_min=payload.approved_version_min,
            category=payload.category,
            risk_level=payload.risk_level,
            classification=payload.classification,
            created_by=updated_by_user_id,
            approval_status="pending_approval",
            version=entry.version + 1,
            supersedes_id=entry.approved_software_id,
        )
        db.add(new_entry)
        db.flush()
        log_action(
            db,
            action=f"Requested reclassification of '{entry.app_name}' from {entry.classification} to {new_entry.classification} — pending approval",
            organization_id=entry.organization_id,
            user_id=updated_by_user_id,
            entity_type="approved_software",
            entity_id=new_entry.approved_software_id,
            old_value={"classification": entry.classification},
            new_value={"classification": new_entry.classification, "supersedes_id": str(entry.approved_software_id)},
        )
        db.commit()
        db.refresh(new_entry)
        return new_entry

    old_value = {"classification": entry.classification, "risk_level": entry.risk_level, "approved_version_min": entry.approved_version_min}
    entry.app_name = payload.app_name
    entry.publisher = payload.publisher
    entry.approved_version_min = payload.approved_version_min
    entry.category = payload.category
    entry.risk_level = payload.risk_level
    entry.classification = payload.classification
    db.flush()
    log_action(
        db,
        action=f"Edited pending classification of '{entry.app_name}' to {entry.classification}",
        organization_id=entry.organization_id,
        user_id=updated_by_user_id,
        entity_type="approved_software",
        entity_id=entry.approved_software_id,
        old_value=old_value,
        new_value={"classification": entry.classification, "risk_level": entry.risk_level, "approved_version_min": entry.approved_version_min},
    )
    db.commit()
    db.refresh(entry)
    return entry


def approve_classification(db: Session, *, entry: ApprovedSoftware, approved_by_user_id: uuid.UUID) -> ApprovedSoftware:
    if entry.approval_status != "pending_approval":
        raise ValueError(f"Cannot approve — this classification is '{entry.approval_status}', not pending approval.")
    if entry.created_by is not None and entry.created_by == approved_by_user_id:
        raise ValueError("You submitted this classification yourself — a different authorized user must approve it.")

    entry.approval_status = "approved"
    entry.approved_by = approved_by_user_id
    entry.approved_at = datetime.now(timezone.utc)
    if entry.supersedes_id is not None:
        superseded = db.get(ApprovedSoftware, entry.supersedes_id)
        if superseded is not None:
            superseded.approval_status = "superseded"
    log_action(
        db,
        action=f"Approved classification of '{entry.app_name}' as {entry.classification}",
        organization_id=entry.organization_id,
        user_id=approved_by_user_id,
        entity_type="approved_software",
        entity_id=entry.approved_software_id,
        new_value={"approval_status": "approved"},
    )
    db.commit()
    db.refresh(entry)
    recheck_all_devices_for_org(db, organization_id=entry.organization_id)
    return entry


def reject_classification(db: Session, *, entry: ApprovedSoftware, reason: str, rejected_by_user_id: uuid.UUID) -> ApprovedSoftware:
    if entry.approval_status != "pending_approval":
        raise ValueError(f"Cannot reject — this classification is '{entry.approval_status}', not pending approval.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reject a classification.")
    if entry.created_by is not None and entry.created_by == rejected_by_user_id:
        raise ValueError("You submitted this classification yourself — a different authorized user must reject it.")

    entry.approval_status = "rejected"
    entry.rejected_by = rejected_by_user_id
    entry.rejected_at = datetime.now(timezone.utc)
    entry.rejected_reason = reason
    log_action(
        db,
        action=f"Rejected classification of '{entry.app_name}' as {entry.classification}: {reason}",
        organization_id=entry.organization_id,
        user_id=rejected_by_user_id,
        entity_type="approved_software",
        entity_id=entry.approved_software_id,
        new_value={"approval_status": "rejected", "reason": reason},
    )
    db.commit()
    db.refresh(entry)
    return entry


def delete_approved_software(db: Session, *, entry: ApprovedSoftware, deleted_by_user_id: uuid.UUID) -> None:
    organization_id = entry.organization_id
    log_action(
        db,
        action=f"Removed '{entry.app_name}' from software policy",
        organization_id=entry.organization_id,
        user_id=deleted_by_user_id,
        entity_type="approved_software",
        entity_id=entry.approved_software_id,
    )
    db.delete(entry)
    db.commit()
    recheck_all_devices_for_org(db, organization_id=organization_id)


def _version_tuple(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in version.strip().split("."))
    except (ValueError, AttributeError):
        return None  # non-numeric version string (e.g. "N/A") — skip the version check, don't guess


def _policy_by_name(policy: list[ApprovedSoftware]) -> dict[str, list[ApprovedSoftware]]:
    by_name: dict[str, list[ApprovedSoftware]] = {}
    for entry in policy:
        by_name.setdefault(entry.app_name.strip().lower(), []).append(entry)
    return by_name


def _match_policy(item: InstalledSoftwareItem, by_name: dict[str, list[ApprovedSoftware]]) -> ApprovedSoftware | None:
    candidates = by_name.get(item.name.strip().lower())
    if not candidates:
        return None
    # Publisher check only applies when the policy row specifies one — a
    # name match with no publisher match falls through to "unknown" rather
    # than a special "spoofed" state; distinguishing those is a good future
    # enhancement (ties into item 4, threat-intel hash lookups) but out of
    # scope for this pass.
    return next((c for c in candidates if not c.publisher or (item.publisher or "").strip().lower() == c.publisher.strip().lower()), None)


def classify_item(item: InstalledSoftwareItem, by_name: dict[str, list[ApprovedSoftware]]) -> tuple[str, str, ApprovedSoftware | None]:
    """Returns (classification, compliance_result, matched_policy_row)."""
    match = _match_policy(item, by_name)
    if match is None:
        return "unknown", "review_required", None

    if match.classification == "restricted":
        return "restricted", "non_compliant", match

    if match.classification in ("approved", "required"):
        if match.approved_version_min and item.version:
            installed_v = _version_tuple(item.version)
            minimum_v = _version_tuple(match.approved_version_min)
            if installed_v is not None and minimum_v is not None and installed_v < minimum_v:
                return match.classification, "outdated", match
        return match.classification, "compliant", match

    if match.classification == "system_component":
        return "system_component", "compliant", match

    if match.classification == "ignored":
        return "ignored", "not_evaluated", match

    if match.classification == "review_required":
        return "review_required", "review_required", match

    return match.classification, "compliant", match


def enrich_installed_software(
    db: Session, *, organization_id: uuid.UUID, items: list[InstalledSoftwareItem]
) -> list[EnrichedSoftwareItem]:
    """For display: classify every item against current policy, live —
    always up to date even if the policy changed since the last telemetry
    report, since this doesn't wait for the next agent check-in."""
    by_name = _policy_by_name(_list_approved_software_in_effect(db, organization_id=organization_id))
    enriched = []
    for item in items:
        classification, compliance_result, _match = classify_item(item, by_name)
        enriched.append(
            EnrichedSoftwareItem(name=item.name, version=item.version, publisher=item.publisher, classification=classification, compliance_result=compliance_result)
        )
    return enriched


def _get_or_create_software_test(db: Session, *, organization_id: uuid.UUID) -> AuditTest:
    test = db.scalar(
        select(AuditTest).where(AuditTest.organization_id == organization_id, AuditTest.test_code == SOFTWARE_TEST_CODE)
    )
    if test is not None:
        return test
    test = AuditTest(
        organization_id=organization_id,
        test_code=SOFTWARE_TEST_CODE,
        test_name=SOFTWARE_TEST_NAME,
        test_description="Auto-created: evaluates each device's reported software inventory against the organization's software policy (approved/required/restricted/system component/ignored/review required).",
        test_type="endpoint_compliance",
        frequency="real_time",
        status="active",
    )
    db.add(test)
    db.flush()
    return test


def check_software_compliance(
    db: Session, *, device: Device, installed_software: list[InstalledSoftwareItem], organization_id: uuid.UUID
) -> TestExecution | None:
    policy = _list_approved_software_in_effect(db, organization_id=organization_id)
    if not policy:
        return None  # nothing approved for this org yet — check stays off

    now = datetime.now(timezone.utc)
    audit_test = _get_or_create_software_test(db, organization_id=organization_id)

    by_name = _policy_by_name(policy)
    flagged: list[tuple[str, str, str]] = []  # (app_label, description, severity)

    for item in installed_software:
        classification, compliance_result, match = classify_item(item, by_name)
        if compliance_result not in _EXCEPTION_RESULTS:
            continue
        if compliance_result == "outdated":
            flagged.append(
                (item.name, f"Outdated application: {item.name} v{item.version} (minimum approved: v{match.approved_version_min})", match.risk_level)
            )
        else:  # non_compliant -> restricted app is present
            flagged.append((item.name, f"Restricted application installed: {item.name}", match.risk_level))

    # 'required' software that isn't installed at all — the other direction
    # a binary allowlist can't express: absence, not presence, is the problem.
    installed_names = {item.name.strip().lower() for item in installed_software}
    for entry in policy:
        if entry.classification == "required" and entry.app_name.strip().lower() not in installed_names:
            flagged.append((entry.app_name, f"Required application missing: {entry.app_name}", entry.risk_level))

    execution = TestExecution(
        audit_test_id=audit_test.audit_test_id,
        started_at=now,
        completed_at=now,
        # Same vocabulary as every other execution path (see
        # app.core.execution_status) — also correctly reads as
        # insufficient_data rather than a false pass if a device ever
        # reports zero installed software.
        status=classify_completed_run(records_analyzed=len(installed_software), exceptions_found=len(flagged)),
        records_analyzed=len(installed_software),
        exceptions_found=len(flagged),
    )
    db.add(execution)
    db.flush()

    evidence_summary = {
        "device_id": str(device.device_id),
        "hostname": device.hostname,
        "installed_count": len(installed_software),
        "policy_count": len(policy),
        "flagged": [description for _label, description, _sev in flagged],
    }
    summary_json = json.dumps(evidence_summary, sort_keys=True)
    db.add(
        Evidence(
            execution_id=execution.execution_id,
            evidence_type="test_result",
            evidence_location=summary_json,
            evidence_hash=fingerprint(summary_json),
        )
    )

    seen_descriptions: set[str] = set()
    for app_label, description, severity in flagged:
        full_description = f"{device.device_name}: {description}"
        seen_descriptions.add(full_description)
        existing = find_open_exception(db, audit_test_id=audit_test.audit_test_id, description=full_description)
        if existing is not None:
            existing.last_detected_at = now
            existing.occurrence_count += 1
            exception_id = existing.exception_id
        else:
            if description.startswith("Required application missing"):
                remediation = f"Install '{app_label}' on this device — it's required by the organization's software policy."
            elif description.startswith("Restricted application installed"):
                remediation = f"Uninstall '{app_label}' from this device — it's restricted by the organization's software policy."
            else:
                remediation = f"Update '{app_label}' to the minimum approved version, or confirm the policy's minimum version is still correct."
            exception_row = Exception_(
                execution_id=execution.execution_id,
                exception_description=full_description,
                recommended_remediation=remediation,
                severity=_RISK_SEVERITY.get(severity, "medium"),
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
                exception_data={"device_id": str(device.device_id), "hostname": device.hostname, "app_name": app_label},
            )
        )

    # Auto-resolve: a condition that was previously flagged and isn't this
    # cycle (uninstalled/installed/updated/reclassified) closes its own
    # exception — same re-test principle as device_compliance_service.
    open_exceptions = db.scalars(
        select(Exception_)
        .join(TestExecution, TestExecution.execution_id == Exception_.execution_id)
        .where(TestExecution.audit_test_id == audit_test.audit_test_id, Exception_.status.in_(OPEN_STATUSES))
    )
    for exc in open_exceptions:
        if exc.exception_description and exc.exception_description.startswith(f"{device.device_name}:") and exc.exception_description not in seen_descriptions:
            exc.status = "resolved"
            exc.last_detected_at = now
            log_action(
                db,
                action=f"Exception auto-resolved: {exc.exception_description} (no longer flagged on re-scan)",
                organization_id=organization_id,
                entity_type="exceptions",
                entity_id=exc.exception_id,
                old_value={"status": "open"},
                new_value={"status": "resolved"},
            )

    db.commit()
    db.refresh(execution)
    return execution
