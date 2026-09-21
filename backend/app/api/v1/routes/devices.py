import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_device, get_current_user, require_permissions
from app.db.session import get_db
from app.models.device import ApprovedSoftware, Device, DeviceCommand, DevicePolicyChange, DeviceSoftwareInventory, DeviceTelemetry
from app.models.rbac import User
from app.schemas.user import EligibleApproverOut
from app.services.auth_service import get_user_permission_names
from app.services.user_service import list_users_with_permission_for_organization
from app.schemas.device import (
    ApprovedSoftwareCreate,
    ApprovedSoftwareOut,
    ApprovedSoftwareRejectRequest,
    CommandResultReport,
    ComplianceCheckDetail,
    DeviceCommandCreate,
    DeviceCommandOut,
    DeviceCreate,
    DeviceCreatedOut,
    DeviceInstructions,
    DeviceLifecycleRequest,
    DeviceOut,
    DevicePolicyOut,
    DevicePolicyChangeOut,
    DevicePolicyRejectRequest,
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    DeviceSoftwareOut,
    DeviceTelemetryOut,
    InstalledSoftwareItem,
    PendingCommandOut,
    TelemetryReport,
)
from app.services.device_command_service import (
    list_commands_for_device,
    list_pending_commands,
    mark_command_sent,
    queue_command,
    report_command_result,
)
from app.services.device_compliance_service import get_compliance_check_details, record_telemetry_and_check_compliance
from app.services.device_policy_service import (
    approve_device_policy_change,
    cancel_device_policy_change,
    get_device_policy,
    list_device_policy_changes,
    reject_device_policy_change,
    request_device_policy_change,
)
from app.services.software_compliance_service import (
    approve_classification,
    cancel_classification,
    create_approved_software,
    delete_approved_software,
    enrich_installed_software,
    list_approved_software,
    reject_classification,
    update_approved_software,
)
from app.services.device_service import (
    approve_deletion,
    approve_revocation,
    cancel_deletion,
    cancel_revocation,
    compliance_status_from_telemetry,
    create_device,
    get_latest_telemetry,
    get_latest_telemetry_bulk,
    list_deleted_devices,
    list_devices,
    redeem_registration_code,
    regenerate_registration_code,
    reject_deletion,
    reject_revocation,
    request_deletion,
    request_revocation,
)
from app.services.endpoint_agent_download_service import windows_exe

router = APIRouter(tags=["devices"])


def _to_out(db: Session, device: Device, out_cls=DeviceOut):
    telemetry = get_latest_telemetry(db, device_id=device.device_id)
    policy = get_device_policy(db, organization_id=device.organization_id)
    return _format_device_out(device, telemetry, policy, out_cls)


def _format_device_out(device: Device, telemetry, policy: dict, out_cls=DeviceOut):
    out = out_cls.model_validate(device)
    return out.model_copy(
        update={
            "antivirus_enabled": telemetry.antivirus_enabled if telemetry else None,
            "firewall_enabled": telemetry.firewall_enabled if telemetry else None,
            "disk_encryption_enabled": telemetry.disk_encryption_enabled if telemetry else None,
            "os_up_to_date": telemetry.os_up_to_date if telemetry else None,
            "compliance_status": compliance_status_from_telemetry(telemetry, policy),
        }
    )


def _to_out_bulk(db: Session, devices: list[Device], out_cls=DeviceOut):
    """Batched equivalent of calling _to_out once per device — the policy
    is a single org-wide value (was being re-fetched identically for every
    device) and telemetry is fetched in one query instead of one per
    device, so an N-device fleet list doesn't pay 2N round trips."""
    if not devices:
        return []
    policy = get_device_policy(db, organization_id=devices[0].organization_id)
    telemetry_by_device = get_latest_telemetry_bulk(db, device_ids=[d.device_id for d in devices])
    return [_format_device_out(d, telemetry_by_device.get(d.device_id), policy, out_cls) for d in devices]


@router.get("/endpoint-agent/download/{platform}")
def download_endpoint_agent(platform: str) -> Response:
    """Public by design — same reasoning as /gateways/download: this ships
    generic installer source, not a credential."""
    try:
        path, filename, media_type = windows_exe(platform)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The Endpoint Agent application isn't available for download right now — please try again shortly.",
        ) from exc
    return FileResponse(path, media_type=media_type, filename=filename)  # streamed from disk, not loaded into memory


@router.post("/organizations/{organization_id}/devices", response_model=DeviceCreatedOut, status_code=status.HTTP_201_CREATED)
def create(
    organization_id: uuid.UUID,
    payload: DeviceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:enrol")),
) -> DeviceCreatedOut:
    enforce_same_organization(organization_id, user, db)
    device = create_device(db, organization_id=organization_id, payload=payload, created_by_user_id=user.user_id)
    return _to_out(db, device, DeviceCreatedOut)


@router.get("/organizations/{organization_id}/devices", response_model=list[DeviceOut])
def list_all(organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[DeviceOut]:
    enforce_same_organization(organization_id, user, db)
    return _to_out_bulk(db, list_devices(db, organization_id=organization_id))


@router.post("/devices/register", response_model=DeviceRegisterResponse)
def register(payload: DeviceRegisterRequest, db: Session = Depends(get_db)):
    """Called by the endpoint agent during enrollment — no user session, only the registration code."""
    try:
        device, api_key = redeem_registration_code(
            db,
            registration_code=payload.registration_code,
            device_name=payload.device_name,
            hostname=payload.hostname,
            os_name=payload.os_name,
            os_version=payload.os_version,
            agent_version=payload.agent_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return DeviceRegisterResponse(device_id=device.device_id, organization_id=device.organization_id, api_key=api_key)


@router.post("/devices/{device_id}/telemetry", response_model=DeviceTelemetryOut)
def report_telemetry(
    device_id: uuid.UUID, payload: TelemetryReport, db: Session = Depends(get_db), device: Device = Depends(get_current_device)
):
    if device.device_id != device_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device credentials do not match this device")
    record_telemetry_and_check_compliance(db, device=device, report=payload)
    # The route's declared response is telemetry-shaped for the agent's own
    # confirmation; the execution/exception chain it triggered is visible to
    # auditors via the normal Executions/Exceptions endpoints, not here.
    return db.scalar(select(DeviceTelemetry).where(DeviceTelemetry.device_id == device_id).order_by(DeviceTelemetry.collected_at.desc()))


@router.get("/organizations/{organization_id}/devices/{device_id}/software", response_model=DeviceSoftwareOut)
def get_software_inventory(
    organization_id: uuid.UUID, device_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> DeviceSoftwareOut:
    enforce_same_organization(organization_id, user, db)
    device = db.get(Device, device_id)
    if device is None or device.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    inventory = db.get(DeviceSoftwareInventory, device_id)
    if inventory is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No software inventory reported yet for this device")
    raw_items = [InstalledSoftwareItem.model_validate(item) for item in inventory.items]
    return DeviceSoftwareOut(
        device_id=inventory.device_id,
        collected_at=inventory.collected_at,
        items=enrich_installed_software(db, organization_id=organization_id, items=raw_items),
    )


@router.get("/organizations/{organization_id}/devices/{device_id}/compliance-detail", response_model=list[ComplianceCheckDetail])
def get_compliance_detail(
    organization_id: uuid.UUID, device_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[ComplianceCheckDetail]:
    device = _get_device_in_organization(db, organization_id, device_id, user)
    return get_compliance_check_details(db, device=device)


@router.get("/organizations/{organization_id}/device-policy", response_model=DevicePolicyOut)
def get_policy(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> DevicePolicyOut:
    enforce_same_organization(organization_id, user, db)
    return DevicePolicyOut(**get_device_policy(db, organization_id=organization_id))


@router.put("/organizations/{organization_id}/device-policy", response_model=DevicePolicyChangeOut, status_code=status.HTTP_202_ACCEPTED)
def request_policy_change(
    organization_id: uuid.UUID,
    payload: DevicePolicyOut,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:manage_policy")),
) -> DevicePolicyChange:
    enforce_same_organization(organization_id, user, db)
    try:
        return request_device_policy_change(
            db, organization_id=organization_id, policy=payload.model_dump(), requested_by_user_id=user.user_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/organizations/{organization_id}/device-policy/changes", response_model=list[DevicePolicyChangeOut])
def list_policy_changes(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[DevicePolicyChange]:
    enforce_same_organization(organization_id, user, db)
    return list_device_policy_changes(db, organization_id=organization_id)


def _get_device_policy_change_or_404(
    db: Session, organization_id: uuid.UUID, policy_change_id: uuid.UUID, user: User
) -> DevicePolicyChange:
    enforce_same_organization(organization_id, user, db)
    change = db.get(DevicePolicyChange, policy_change_id)
    if change is None or change.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device policy change not found")
    return change


@router.post(
    "/organizations/{organization_id}/device-policy/changes/{policy_change_id}/approve",
    response_model=DevicePolicyChangeOut,
)
def approve_policy_change(
    organization_id: uuid.UUID,
    policy_change_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:approve_policy")),
) -> DevicePolicyChange:
    change = _get_device_policy_change_or_404(db, organization_id, policy_change_id, user)
    try:
        return approve_device_policy_change(db, change=change, approved_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post(
    "/organizations/{organization_id}/device-policy/changes/{policy_change_id}/reject",
    response_model=DevicePolicyChangeOut,
)
def reject_policy_change(
    organization_id: uuid.UUID,
    policy_change_id: uuid.UUID,
    payload: DevicePolicyRejectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:approve_policy")),
) -> DevicePolicyChange:
    change = _get_device_policy_change_or_404(db, organization_id, policy_change_id, user)
    try:
        return reject_device_policy_change(db, change=change, reason=payload.reason, rejected_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post(
    "/organizations/{organization_id}/device-policy/changes/{policy_change_id}/cancel",
    response_model=DevicePolicyChangeOut,
)
def cancel_policy_change(
    organization_id: uuid.UUID,
    policy_change_id: uuid.UUID,
    payload: DevicePolicyRejectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:manage_policy")),
) -> DevicePolicyChange:
    change = _get_device_policy_change_or_404(db, organization_id, policy_change_id, user)
    try:
        return cancel_device_policy_change(db, change=change, reason=payload.reason, cancelled_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.get("/organizations/{organization_id}/approved-software", response_model=list[ApprovedSoftwareOut])
def list_approved_software_route(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[ApprovedSoftware]:
    enforce_same_organization(organization_id, user, db)
    return list_approved_software(db, organization_id=organization_id)


@router.post(
    "/organizations/{organization_id}/approved-software", response_model=ApprovedSoftwareOut, status_code=status.HTTP_201_CREATED
)
def create_approved_software_route(
    organization_id: uuid.UUID,
    payload: ApprovedSoftwareCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:manage_policy")),
) -> ApprovedSoftware:
    enforce_same_organization(organization_id, user, db)
    return create_approved_software(db, organization_id=organization_id, payload=payload, created_by_user_id=user.user_id)


@router.patch("/organizations/{organization_id}/approved-software/{approved_software_id}", response_model=ApprovedSoftwareOut)
def update_approved_software_route(
    organization_id: uuid.UUID,
    approved_software_id: uuid.UUID,
    payload: ApprovedSoftwareCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:manage_policy")),
) -> ApprovedSoftware:
    enforce_same_organization(organization_id, user, db)
    entry = db.get(ApprovedSoftware, approved_software_id)
    if entry is None or entry.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approved software entry not found")
    return update_approved_software(db, entry=entry, payload=payload, updated_by_user_id=user.user_id)


@router.delete("/organizations/{organization_id}/approved-software/{approved_software_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_approved_software_route(
    organization_id: uuid.UUID,
    approved_software_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:manage_policy")),
) -> None:
    enforce_same_organization(organization_id, user, db)
    entry = db.get(ApprovedSoftware, approved_software_id)
    if entry is None or entry.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approved software entry not found")
    delete_approved_software(db, entry=entry, deleted_by_user_id=user.user_id)


def _get_approved_software_or_404(db: Session, organization_id: uuid.UUID, approved_software_id: uuid.UUID, user: User) -> ApprovedSoftware:
    enforce_same_organization(organization_id, user, db)
    entry = db.get(ApprovedSoftware, approved_software_id)
    if entry is None or entry.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approved software entry not found")
    return entry


@router.post("/organizations/{organization_id}/approved-software/{approved_software_id}/approve", response_model=ApprovedSoftwareOut)
def approve_approved_software_route(
    organization_id: uuid.UUID, approved_software_id: uuid.UUID, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:manage_policy")),
) -> ApprovedSoftware:
    entry = _get_approved_software_or_404(db, organization_id, approved_software_id, user)
    try:
        return approve_classification(db, entry=entry, approved_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post("/organizations/{organization_id}/approved-software/{approved_software_id}/reject", response_model=ApprovedSoftwareOut)
def reject_approved_software_route(
    organization_id: uuid.UUID, approved_software_id: uuid.UUID, payload: ApprovedSoftwareRejectRequest,
    db: Session = Depends(get_db), user: User = Depends(require_permissions("devices:manage_policy")),
) -> ApprovedSoftware:
    entry = _get_approved_software_or_404(db, organization_id, approved_software_id, user)
    try:
        return reject_classification(db, entry=entry, reason=payload.reason, rejected_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post("/organizations/{organization_id}/approved-software/{approved_software_id}/cancel", response_model=ApprovedSoftwareOut)
def cancel_approved_software_route(
    organization_id: uuid.UUID, approved_software_id: uuid.UUID, payload: ApprovedSoftwareRejectRequest,
    db: Session = Depends(get_db), user: User = Depends(require_permissions("devices:manage_policy")),
) -> ApprovedSoftware:
    entry = _get_approved_software_or_404(db, organization_id, approved_software_id, user)
    try:
        return cancel_classification(db, entry=entry, reason=payload.reason, cancelled_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def _get_device_in_organization(db: Session, organization_id: uuid.UUID, device_id: uuid.UUID, user: User) -> Device:
    enforce_same_organization(organization_id, user, db)
    device = db.get(Device, device_id)
    if device is None or device.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


@router.get("/organizations/{organization_id}/devices/{device_id}/commands", response_model=list[DeviceCommandOut])
def list_device_commands(
    organization_id: uuid.UUID, device_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[DeviceCommand]:
    _get_device_in_organization(db, organization_id, device_id, user)
    return list_commands_for_device(db, device_id=device_id)


# Which permission a command type requires — "safe" commands (a compliance
# re-check) execute immediately; "disruptive" ones (restart) don't yet
# require separate approval before reaching the device (that's the
# devices:approve_disruptive_command permission, catalogued but not
# enforced here — a real gap flagged for the next pass, not an oversight).
_COMMAND_PERMISSION_BY_TYPE = {"run_check_now": "devices:command_safe", "restart": "devices:command_disruptive"}


@router.post(
    "/organizations/{organization_id}/devices/{device_id}/commands",
    response_model=DeviceCommandOut,
    status_code=status.HTTP_201_CREATED,
)
def create_device_command(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    payload: DeviceCommandCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DeviceCommand:
    required_permission = _COMMAND_PERMISSION_BY_TYPE.get(payload.command_type)
    if required_permission is None or required_permission not in get_user_permission_names(db, user.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing required permission(s): {required_permission}")
    device = _get_device_in_organization(db, organization_id, device_id, user)
    try:
        return queue_command(db, device=device, command_type=payload.command_type, requested_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/devices/{device_id}/instructions", response_model=DeviceInstructions)
def get_instructions(
    device_id: uuid.UUID, db: Session = Depends(get_db), device: Device = Depends(get_current_device)
) -> DeviceInstructions:
    """Polled by the agent right after each telemetry report — delivers the
    org's current compliance policy and any commands awaiting execution."""
    if device.device_id != device_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device credentials do not match this device")
    pending = list_pending_commands(db, device_id=device_id)
    mark_command_sent(db, commands=pending)
    return DeviceInstructions(
        policy=DevicePolicyOut(**get_device_policy(db, organization_id=device.organization_id)),
        pending_commands=[PendingCommandOut(command_id=c.command_id, command_type=c.command_type) for c in pending],
    )


@router.post("/devices/{device_id}/commands/{command_id}/result", response_model=DeviceCommandOut)
def report_command_result_route(
    device_id: uuid.UUID,
    command_id: uuid.UUID,
    payload: CommandResultReport,
    db: Session = Depends(get_db),
    device: Device = Depends(get_current_device),
) -> DeviceCommand:
    if device.device_id != device_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device credentials do not match this device")
    command = db.get(DeviceCommand, command_id)
    if command is None or command.device_id != device_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Command not found")
    return report_command_result(db, command=command, success=payload.success, message=payload.message)


def _get_device_or_404(db: Session, device_id: uuid.UUID, user: User) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    enforce_same_organization(device.organization_id, user, db)
    return device


@router.post("/devices/{device_id}/revocation/request", response_model=DeviceOut)
def request_device_revocation(
    device_id: uuid.UUID, payload: DeviceLifecycleRequest, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:request_revoke")),
) -> DeviceOut:
    device = _get_device_or_404(db, device_id, user)
    try:
        updated = request_revocation(db, device=device, reason=payload.reason, requested_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/devices/{device_id}/revocation/approve", response_model=DeviceOut)
def approve_device_revocation(
    device_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_permissions("devices:approve_revoke"))
) -> DeviceOut:
    device = _get_device_or_404(db, device_id, user)
    try:
        updated = approve_revocation(db, device=device, approved_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/devices/{device_id}/revocation/reject", response_model=DeviceOut)
def reject_device_revocation(
    device_id: uuid.UUID, payload: DeviceLifecycleRequest, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:approve_revoke")),
) -> DeviceOut:
    device = _get_device_or_404(db, device_id, user)
    try:
        updated = reject_revocation(db, device=device, reason=payload.reason, rejected_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/devices/{device_id}/revocation/cancel", response_model=DeviceOut)
def cancel_device_revocation(
    device_id: uuid.UUID, payload: DeviceLifecycleRequest, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:request_revoke")),
) -> DeviceOut:
    device = _get_device_or_404(db, device_id, user)
    try:
        updated = cancel_revocation(db, device=device, reason=payload.reason, cancelled_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/devices/{device_id}/deletion/request", response_model=DeviceOut)
def request_device_deletion(
    device_id: uuid.UUID, payload: DeviceLifecycleRequest, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:request_delete")),
) -> DeviceOut:
    device = _get_device_or_404(db, device_id, user)
    try:
        updated = request_deletion(db, device=device, reason=payload.reason, requested_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/devices/{device_id}/deletion/approve", response_model=DeviceOut)
def approve_device_deletion(
    device_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_permissions("devices:approve_delete"))
) -> DeviceOut:
    device = _get_device_or_404(db, device_id, user)
    try:
        updated = approve_deletion(db, device=device, approved_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/devices/{device_id}/deletion/reject", response_model=DeviceOut)
def reject_device_deletion(
    device_id: uuid.UUID, payload: DeviceLifecycleRequest, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:approve_delete")),
) -> DeviceOut:
    device = _get_device_or_404(db, device_id, user)
    try:
        updated = reject_deletion(db, device=device, reason=payload.reason, rejected_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/devices/{device_id}/deletion/cancel", response_model=DeviceOut)
def cancel_device_deletion(
    device_id: uuid.UUID, payload: DeviceLifecycleRequest, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("devices:request_delete")),
) -> DeviceOut:
    device = _get_device_or_404(db, device_id, user)
    try:
        updated = cancel_deletion(db, device=device, reason=payload.reason, cancelled_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.get("/organizations/{organization_id}/devices/deleted", response_model=list[DeviceOut])
def list_deleted_devices_route(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[DeviceOut]:
    enforce_same_organization(organization_id, user, db)
    return _to_out_bulk(db, list_deleted_devices(db, organization_id=organization_id))


_APPROVER_PERMISSION_BY_ACTION = {
    "revoke": "devices:approve_revoke",
    "delete": "devices:approve_delete",
    "classify": "devices:manage_policy",
    "policy": "devices:approve_policy",
}


@router.get("/organizations/{organization_id}/devices/eligible-approvers", response_model=list[EligibleApproverOut])
def list_eligible_device_approvers(
    organization_id: uuid.UUID, action: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[User]:
    """Who a revocation/deletion request will actually be sent to — real
    names instead of a vague "a different authorized user" placeholder.
    `action` is 'revoke' or 'delete' — each has its own, narrower approver
    permission now (see migration 0034)."""
    enforce_same_organization(organization_id, user, db)
    permission_name = _APPROVER_PERMISSION_BY_ACTION.get(action)
    if permission_name is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown approval action")
    return list_users_with_permission_for_organization(db, organization_id=organization_id, permission_name=permission_name)


@router.post("/devices/{device_id}/regenerate-code", response_model=DeviceCreatedOut)
def regenerate_code(
    device_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_permissions("devices:enrol"))
) -> DeviceCreatedOut:
    device = _get_device_or_404(db, device_id, user)
    updated = regenerate_registration_code(db, device=device, requested_by_user_id=user.user_id)
    return _to_out(db, updated, DeviceCreatedOut)
