import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.rbac import User
from app.models.risk_control import Control
from app.schemas.control import ControlActivateRequest, ControlDeactivationRequest, ControlOut, ControlRejectRequest
from app.schemas.control_binding import (
    ControlTableBindingCreate,
    ControlTableBindingOut,
    ControlTableNotApplicable,
    TableBindingProgressOut,
)
from app.services.control_binding_service import bind_table, get_binding_progress, mark_not_applicable, unbind_table
from app.services.control_service import (
    activate_control,
    approve_activation,
    approve_deactivation,
    describe_controls,
    get_domain_and_tables,
    get_risk_ids_for_control,
    list_controls,
    reject_activation,
    reject_deactivation,
    request_activation,
    request_deactivation,
)

router = APIRouter(prefix="/organizations/{organization_id}/controls", tags=["controls"])


def _get_control_or_404(db: Session, organization_id: uuid.UUID, control_id: uuid.UUID) -> Control:
    control = db.get(Control, control_id)
    if control is None or control.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Control not found")
    return control


def _to_out(db: Session, control: Control) -> ControlOut:
    domain, required_tables = get_domain_and_tables(db, control_library_id=control.control_library_id)
    out = ControlOut.model_validate(control)
    return out.model_copy(
        update={
            "risk_ids": get_risk_ids_for_control(db, control_id=control.control_id),
            "domain": domain,
            "required_tables": required_tables,
        }
    )


@router.post("/activate", response_model=ControlOut, status_code=status.HTTP_201_CREATED)
def activate(
    organization_id: uuid.UUID,
    payload: ControlActivateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> ControlOut:
    enforce_same_organization(organization_id, user, db)
    try:
        control = activate_control(db, organization_id=organization_id, payload=payload, created_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, control)


@router.get("", response_model=list[ControlOut])
def list_all(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[ControlOut]:
    enforce_same_organization(organization_id, user, db)
    controls = list_controls(db, organization_id=organization_id)
    described = describe_controls(db, controls=controls)
    out = []
    for control in controls:
        risk_ids, domain, required_tables = described.get(control.control_id, ([], None, []))
        row = ControlOut.model_validate(control)
        out.append(row.model_copy(update={"risk_ids": risk_ids, "domain": domain, "required_tables": required_tables}))
    return out


@router.post("/{control_id}/activation/request", response_model=ControlOut)
def activation_request(
    organization_id: uuid.UUID, control_id: uuid.UUID, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> ControlOut:
    enforce_same_organization(organization_id, user, db)
    control = _get_control_or_404(db, organization_id, control_id)
    try:
        updated = request_activation(db, control=control, requested_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{control_id}/activation/approve", response_model=ControlOut)
def activation_approve(
    organization_id: uuid.UUID, control_id: uuid.UUID, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> ControlOut:
    enforce_same_organization(organization_id, user, db)
    control = _get_control_or_404(db, organization_id, control_id)
    try:
        updated = approve_activation(db, control=control, approved_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{control_id}/activation/reject", response_model=ControlOut)
def activation_reject(
    organization_id: uuid.UUID, control_id: uuid.UUID, payload: ControlRejectRequest, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> ControlOut:
    enforce_same_organization(organization_id, user, db)
    control = _get_control_or_404(db, organization_id, control_id)
    try:
        updated = reject_activation(db, control=control, reason=payload.reason, rejected_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{control_id}/deactivation/request", response_model=ControlOut)
def deactivation_request(
    organization_id: uuid.UUID, control_id: uuid.UUID, payload: ControlDeactivationRequest, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> ControlOut:
    enforce_same_organization(organization_id, user, db)
    control = _get_control_or_404(db, organization_id, control_id)
    try:
        updated = request_deactivation(db, control=control, reason=payload.reason, requested_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{control_id}/deactivation/approve", response_model=ControlOut)
def deactivation_approve(
    organization_id: uuid.UUID, control_id: uuid.UUID, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> ControlOut:
    enforce_same_organization(organization_id, user, db)
    control = _get_control_or_404(db, organization_id, control_id)
    try:
        updated = approve_deactivation(db, control=control, approved_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.post("/{control_id}/deactivation/reject", response_model=ControlOut)
def deactivation_reject(
    organization_id: uuid.UUID, control_id: uuid.UUID, payload: ControlRejectRequest, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> ControlOut:
    enforce_same_organization(organization_id, user, db)
    control = _get_control_or_404(db, organization_id, control_id)
    try:
        updated = reject_deactivation(db, control=control, reason=payload.reason, rejected_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(db, updated)


@router.get("/{control_id}/table-bindings", response_model=TableBindingProgressOut)
def get_table_bindings(
    organization_id: uuid.UUID, control_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> TableBindingProgressOut:
    enforce_same_organization(organization_id, user, db)
    control = _get_control_or_404(db, organization_id, control_id)
    return get_binding_progress(db, control=control)


@router.post("/{control_id}/table-bindings", response_model=ControlTableBindingOut, status_code=status.HTTP_201_CREATED)
def create_table_binding(
    organization_id: uuid.UUID,
    control_id: uuid.UUID,
    payload: ControlTableBindingCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> ControlTableBindingOut:
    enforce_same_organization(organization_id, user, db)
    control = _get_control_or_404(db, organization_id, control_id)
    try:
        binding = bind_table(db, control=control, payload=payload, bound_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    progress = get_binding_progress(db, control=control)
    return next(b for b in progress.bindings if b.binding_id == binding.binding_id)


@router.post("/{control_id}/table-bindings/not-applicable", response_model=ControlTableBindingOut, status_code=status.HTTP_201_CREATED)
def create_table_not_applicable(
    organization_id: uuid.UUID,
    control_id: uuid.UUID,
    payload: ControlTableNotApplicable,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> ControlTableBindingOut:
    enforce_same_organization(organization_id, user, db)
    control = _get_control_or_404(db, organization_id, control_id)
    try:
        binding = mark_not_applicable(db, control=control, payload=payload, bound_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    progress = get_binding_progress(db, control=control)
    return next(b for b in progress.bindings if b.binding_id == binding.binding_id)


@router.delete("/{control_id}/table-bindings/{canonical_table_name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_table_binding(
    organization_id: uuid.UUID,
    control_id: uuid.UUID,
    canonical_table_name: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> None:
    enforce_same_organization(organization_id, user, db)
    control = _get_control_or_404(db, organization_id, control_id)
    try:
        unbind_table(db, control=control, canonical_table_name=canonical_table_name, unbound_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
