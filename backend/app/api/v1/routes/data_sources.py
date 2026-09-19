import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_gateway, get_current_user, require_permissions
from app.db.session import get_db
from app.models.data_source import DataConnection, DataConnectionChange, DataEntity, DataRelationship, DataSource, Gateway
from app.models.rbac import User
from app.schemas.data_source import (
    ConnectionTestResult,
    DataConnectionChangeOut,
    DataConnectionCreate,
    DataConnectionOut,
    DataConnectionRejectRequest,
    DataConnectionUpdateRequest,
    DataEntityOut,
    DataFieldOut,
    DataSourceCreate,
    DataSourceOut,
    DirectConnectionCreate,
    DiscoveryPayload,
    HiddenToggleRequest,
    RelationshipMeasurementsIn,
    RelationshipRequestOut,
)
from app.schemas.data_mapping import RelationshipRuling
from app.schemas.user import EligibleApproverOut
from app.services.audit_log_service import log_action
from app.services.gateway_relationship_service import relationship_requests_for_gateway, store_gateway_measurements
from app.services.data_connection_change_service import (
    approve_connection_change,
    cancel_connection_change,
    list_changes_for_connection,
    reject_connection_change,
    request_connection_delete,
    request_connection_disconnect,
    request_connection_update,
)
from app.services.data_source_service import (
    create_connection,
    create_data_source,
    create_direct_connection,
    discover_direct_connection_schema,
    list_connections,
    list_connections_for_organization,
    list_data_sources,
    list_entities,
    list_entities_for_organization,
    infer_relationships_in_background,
    list_fields,
    profile_data_source_in_background,
    profile_entity_columns,
    record_connection_test_result,
    replace_discovery,
    set_all_connections_hidden,
    set_all_entities_hidden,
    set_connection_hidden,
    set_entity_hidden,
    test_direct_connection,
)
from app.services.user_service import list_users_with_permission_for_organization

router = APIRouter(tags=["data-sources"])


@router.post("/organizations/{organization_id}/data-sources", response_model=DataSourceOut, status_code=status.HTTP_201_CREATED)
def create_source(
    organization_id: uuid.UUID,
    payload: DataSourceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("data_sources:manage")),
) -> DataSource:
    enforce_same_organization(organization_id, user, db)
    return create_data_source(db, organization_id=organization_id, payload=payload, created_by_user_id=user.user_id)


@router.get("/organizations/{organization_id}/data-sources", response_model=list[DataSourceOut])
def list_sources(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[DataSource]:
    enforce_same_organization(organization_id, user, db)
    return list_data_sources(db, organization_id=organization_id)


@router.get("/organizations/{organization_id}/connections", response_model=list[DataConnectionOut])
def list_all_connections(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[DataConnection]:
    enforce_same_organization(organization_id, user, db)
    return list_connections_for_organization(db, organization_id=organization_id)


@router.get("/organizations/{organization_id}/data-catalogue", response_model=list[DataEntityOut])
def list_data_catalogue(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    enforce_same_organization(organization_id, user, db)
    return list_entities_for_organization(db, organization_id=organization_id)


def _get_source_or_404(db: Session, data_source_id: uuid.UUID) -> DataSource:
    source = db.get(DataSource, data_source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")
    return source


@router.post("/data-sources/{data_source_id}/connections", response_model=DataConnectionOut, status_code=status.HTTP_201_CREATED)
def create_connection_route(
    data_source_id: uuid.UUID,
    payload: DataConnectionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("data_sources:manage")),
):
    source = _get_source_or_404(db, data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    try:
        return create_connection(
            db, data_source_id=data_source_id, payload=payload, created_by_user_id=user.user_id,
            organization_id=source.organization_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/data-sources/{data_source_id}/connections", response_model=list[DataConnectionOut])
def list_connections_route(data_source_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    source = _get_source_or_404(db, data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    return list_connections(db, data_source_id=data_source_id)


@router.post(
    "/data-sources/{data_source_id}/connections/direct", response_model=DataConnectionOut, status_code=status.HTTP_201_CREATED
)
def create_direct_connection_route(
    data_source_id: uuid.UUID,
    payload: DirectConnectionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("data_sources:manage")),
):
    source = _get_source_or_404(db, data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    try:
        return create_direct_connection(
            db, data_source_id=data_source_id, payload=payload, created_by_user_id=user.user_id,
            organization_id=source.organization_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _get_connection_in_organization(db: Session, connection_id: uuid.UUID, user: User) -> DataConnection:
    connection = db.get(DataConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    source = _get_source_or_404(db, connection.data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    return connection


@router.post("/connections/{connection_id}/test", response_model=ConnectionTestResult)
def test_connection_route(
    connection_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_permissions("data_sources:manage"))
):
    connection = _get_connection_in_organization(db, connection_id, user)
    if connection.connection_mode != "direct":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Gateway-based connections are tested by the Gateway itself, not from the platform.",
        )
    success, detail = test_direct_connection(db, connection=connection)
    return ConnectionTestResult(success=success, detail=detail)


@router.post("/connections/{connection_id}/discover", response_model=list[DataEntityOut])
def discover_connection_route(
    connection_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_permissions("data_sources:manage"))
):
    connection = _get_connection_in_organization(db, connection_id, user)
    if connection.connection_mode != "direct":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Gateway-based connections report discovery from the Gateway itself, not from the platform.",
        )
    try:
        entities = discover_direct_connection_schema(db, connection=connection)
        # Reading what the columns actually hold is background work — a big
        # source has hundreds of tables and discovery must not wait for it.
        threading.Thread(
            target=profile_data_source_in_background, args=(connection.data_source_id,), daemon=True, name="column-profiling"
        ).start()
        return entities
    except Exception as exc:  # noqa: BLE001 — surfaced as a clean 400, never a raw driver error with the DSN in it
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not read the schema from this database. Test the connection first.") from exc


@router.patch("/data-sources/{data_source_id}/connections/hidden")
def set_all_connections_hidden_route(
    data_source_id: uuid.UUID,
    payload: HiddenToggleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    source = _get_source_or_404(db, data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    count = set_all_connections_hidden(
        db, data_source_id=data_source_id, hidden=payload.hidden, organization_id=source.organization_id, updated_by_user_id=user.user_id
    )
    return {"updated": count}


@router.patch("/connections/{connection_id}/hidden", response_model=DataConnectionOut)
def set_connection_hidden_route(
    connection_id: uuid.UUID,
    payload: HiddenToggleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Any org member can declutter their own view of a connection list —
    this never changes what the connection actually does, so it doesn't
    need data_sources:manage, only membership in the same organization."""
    connection = _get_connection_in_organization(db, connection_id, user)
    source = _get_source_or_404(db, connection.data_source_id)
    return set_connection_hidden(
        db, connection=connection, hidden=payload.hidden, organization_id=source.organization_id, updated_by_user_id=user.user_id
    )


def _get_pending_change_or_404(db: Session, connection_id: uuid.UUID, change_id: uuid.UUID) -> DataConnectionChange:
    change = db.get(DataConnectionChange, change_id)
    if change is None or change.connection_id != connection_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data connection change not found")
    return change


@router.get("/connections/{connection_id}/changes", response_model=list[DataConnectionChangeOut])
def list_connection_changes_route(
    connection_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    _get_connection_in_organization(db, connection_id, user)
    return list_changes_for_connection(db, connection_id=connection_id)


@router.post(
    "/connections/{connection_id}/changes/update", response_model=DataConnectionChangeOut, status_code=status.HTTP_202_ACCEPTED
)
def request_connection_update_route(
    connection_id: uuid.UUID,
    payload: DataConnectionUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("data_sources:manage")),
):
    connection = _get_connection_in_organization(db, connection_id, user)
    source = _get_source_or_404(db, connection.data_source_id)
    try:
        return request_connection_update(
            db, connection=connection, payload=payload, requested_by_user_id=user.user_id, organization_id=source.organization_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/connections/{connection_id}/changes/disconnect", response_model=DataConnectionChangeOut, status_code=status.HTTP_202_ACCEPTED
)
def request_connection_disconnect_route(
    connection_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("data_sources:manage")),
):
    connection = _get_connection_in_organization(db, connection_id, user)
    source = _get_source_or_404(db, connection.data_source_id)
    try:
        return request_connection_disconnect(
            db, connection=connection, requested_by_user_id=user.user_id, organization_id=source.organization_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/connections/{connection_id}/changes/delete", response_model=DataConnectionChangeOut, status_code=status.HTTP_202_ACCEPTED
)
def request_connection_delete_route(
    connection_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("data_sources:manage")),
):
    connection = _get_connection_in_organization(db, connection_id, user)
    source = _get_source_or_404(db, connection.data_source_id)
    try:
        return request_connection_delete(
            db, connection=connection, requested_by_user_id=user.user_id, organization_id=source.organization_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/changes/{change_id}/approve", response_model=DataConnectionChangeOut)
def approve_connection_change_route(
    connection_id: uuid.UUID,
    change_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("data_sources:approve_change")),
):
    connection = _get_connection_in_organization(db, connection_id, user)
    source = _get_source_or_404(db, connection.data_source_id)
    change = _get_pending_change_or_404(db, connection_id, change_id)
    try:
        return approve_connection_change(
            db, change=change, connection=connection, approved_by_user_id=user.user_id, organization_id=source.organization_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/changes/{change_id}/reject", response_model=DataConnectionChangeOut)
def reject_connection_change_route(
    connection_id: uuid.UUID,
    change_id: uuid.UUID,
    payload: DataConnectionRejectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("data_sources:approve_change")),
):
    connection = _get_connection_in_organization(db, connection_id, user)
    source = _get_source_or_404(db, connection.data_source_id)
    change = _get_pending_change_or_404(db, connection_id, change_id)
    try:
        return reject_connection_change(
            db, change=change, reason=payload.reason, rejected_by_user_id=user.user_id, organization_id=source.organization_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/changes/{change_id}/cancel", response_model=DataConnectionChangeOut)
def cancel_connection_change_route(
    connection_id: uuid.UUID,
    change_id: uuid.UUID,
    payload: DataConnectionRejectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("data_sources:manage")),
):
    connection = _get_connection_in_organization(db, connection_id, user)
    source = _get_source_or_404(db, connection.data_source_id)
    change = _get_pending_change_or_404(db, connection_id, change_id)
    try:
        return cancel_connection_change(
            db, change=change, reason=payload.reason, cancelled_by_user_id=user.user_id, organization_id=source.organization_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.get("/organizations/{organization_id}/data-sources/eligible-approvers", response_model=list[EligibleApproverOut])
def list_eligible_connection_change_approvers(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[User]:
    enforce_same_organization(organization_id, user, db)
    return list_users_with_permission_for_organization(db, organization_id=organization_id, permission_name="data_sources:approve_change")


@router.get("/data-sources/{data_source_id}/entities", response_model=list[DataEntityOut])
def list_entities_route(data_source_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    source = _get_source_or_404(db, data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    return list_entities(db, data_source_id=data_source_id)


@router.patch("/data-sources/{data_source_id}/entities/hidden")
def set_all_entities_hidden_route(
    data_source_id: uuid.UUID,
    payload: HiddenToggleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    source = _get_source_or_404(db, data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    count = set_all_entities_hidden(
        db, data_source_id=data_source_id, hidden=payload.hidden, organization_id=source.organization_id, updated_by_user_id=user.user_id
    )
    return {"updated": count}


@router.patch("/data-sources/{data_source_id}/entities/{entity_id}/hidden", response_model=DataEntityOut)
def set_entity_hidden_route(
    data_source_id: uuid.UUID,
    entity_id: uuid.UUID,
    payload: HiddenToggleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    source = _get_source_or_404(db, data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    entity = db.get(DataEntity, entity_id)
    if entity is None or entity.data_source_id != data_source_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    return set_entity_hidden(
        db, entity=entity, hidden=payload.hidden, organization_id=source.organization_id, updated_by_user_id=user.user_id
    )


@router.post("/data-sources/{data_source_id}/profile", status_code=status.HTTP_202_ACCEPTED)
def profile_data_source_route(
    data_source_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_permissions("data_sources:manage"))
) -> dict:
    """Starts reading what every table's columns actually hold (see
    app/core/value_profile.py). Runs in the background; results show up on
    the mapping and table-binding screens as they complete."""
    source = _get_source_or_404(db, data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    threading.Thread(
        target=profile_data_source_in_background, args=(data_source_id,), daemon=True, name="column-profiling"
    ).start()
    return {"started": True}


@router.post("/data-sources/{data_source_id}/relationships/refresh", status_code=status.HTTP_202_ACCEPTED)
def refresh_relationships_route(
    data_source_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_permissions("data_sources:manage"))
) -> dict:
    """Re-measures how this source's tables relate (declared foreign keys are
    read at discovery; this measures the undeclared ones on the client's own
    data). Runs in the background; needs a connected direct connection."""
    source = _get_source_or_404(db, data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    # A Gateway measures for itself: clearing its stamp makes its next poll ask again.
    db.execute(
        update(DataConnection)
        .where(DataConnection.data_source_id == data_source_id, DataConnection.gateway_id.is_not(None))
        .values(relationships_measured_at=None)
    )
    db.commit()
    threading.Thread(
        target=infer_relationships_in_background, args=(data_source_id,), daemon=True, name="relationship-inference"
    ).start()
    return {"started": True}


@router.patch("/relationships/{relationship_id}")
def rule_on_relationship_route(
    relationship_id: uuid.UUID,
    payload: RelationshipRuling,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> dict:
    """An auditor confirms or rejects a detected relationship. Neither
    re-discovery nor re-inference ever overwrites that ruling."""
    relationship = db.get(DataRelationship, relationship_id)
    if relationship is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found")
    source = _get_source_or_404(db, relationship.data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    old_status = relationship.status
    relationship.status = payload.status
    log_action(
        db,
        action=f"Set a detected table relationship to '{payload.status}'",
        organization_id=source.organization_id,
        user_id=user.user_id,
        entity_type="data_relationships",
        entity_id=relationship.relationship_id,
        old_value={"status": old_status},
        new_value={"status": payload.status},
    )
    db.commit()
    return {"relationship_id": str(relationship_id), "status": relationship.status}


@router.post("/entities/{entity_id}/profile")
def profile_entity_route(
    entity_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_permissions("data_sources:manage"))
) -> dict:
    entity = db.get(DataEntity, entity_id)
    if entity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    source = _get_source_or_404(db, entity.data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    profiled = profile_entity_columns(db, entity_id=entity_id)
    if profiled is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read this table from the platform. This needs a connected direct connection; a Gateway profiles its own tables and sends the result with its discovery.",
        )
    return {"profiled": profiled}


@router.get("/entities/{entity_id}/fields", response_model=list[DataFieldOut])
def list_fields_route(entity_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    entity = db.get(DataEntity, entity_id)
    if entity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    source = _get_source_or_404(db, entity.data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    return list_fields(db, entity_id=entity_id)


def _get_connection_owned_by_gateway(db: Session, connection_id: uuid.UUID, gateway: Gateway) -> DataConnection:
    connection = db.get(DataConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    if connection.gateway_id != gateway.gateway_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This gateway is not assigned to that connection")
    return connection


@router.post("/gateways/{gateway_id}/connections/{connection_id}/test-result", response_model=DataConnectionOut)
def report_test_result(
    gateway_id: uuid.UUID,
    connection_id: uuid.UUID,
    payload: ConnectionTestResult,
    db: Session = Depends(get_db),
    gateway: Gateway = Depends(get_current_gateway),
):
    if gateway.gateway_id != gateway_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Gateway credentials do not match this gateway")
    connection = _get_connection_owned_by_gateway(db, connection_id, gateway)
    return record_connection_test_result(db, connection=connection, success=payload.success)


@router.get("/gateways/{gateway_id}/relationship-requests", response_model=list[RelationshipRequestOut])
def get_relationship_requests(
    gateway_id: uuid.UUID, db: Session = Depends(get_db), gateway: Gateway = Depends(get_current_gateway)
) -> list[RelationshipRequestOut]:
    """The column pairs this Gateway should measure on its own database now (see
    gateway_relationship_service). Pull-based like due tests: the Gateway asks."""
    if gateway.gateway_id != gateway_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your gateway")
    return relationship_requests_for_gateway(db, gateway_id=gateway_id)


@router.post("/gateways/{gateway_id}/connections/{connection_id}/relationship-measurements")
def report_relationship_measurements(
    gateway_id: uuid.UUID,
    connection_id: uuid.UUID,
    payload: RelationshipMeasurementsIn,
    db: Session = Depends(get_db),
    gateway: Gateway = Depends(get_current_gateway),
) -> dict:
    """Counts a Gateway measured (never values). Only pairs the platform asked for
    are accepted; see store_gateway_measurements."""
    if gateway.gateway_id != gateway_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your gateway")
    connection = _get_connection_owned_by_gateway(db, connection_id, gateway)
    return {"stored": store_gateway_measurements(db, connection=connection, measurements=payload.measurements)}


@router.post("/gateways/{gateway_id}/connections/{connection_id}/discovery", response_model=list[DataEntityOut])
def report_discovery(
    gateway_id: uuid.UUID,
    connection_id: uuid.UUID,
    payload: DiscoveryPayload,
    db: Session = Depends(get_db),
    gateway: Gateway = Depends(get_current_gateway),
):
    if gateway.gateway_id != gateway_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Gateway credentials do not match this gateway")
    connection = _get_connection_owned_by_gateway(db, connection_id, gateway)
    return replace_discovery(db, data_source_id=connection.data_source_id, payload=payload)
