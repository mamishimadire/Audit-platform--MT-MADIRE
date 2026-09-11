import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_gateway, get_current_user, require_permissions
from app.db.session import get_db
from app.models.data_source import DataConnection, DataEntity, DataSource, Gateway
from app.models.rbac import User
from app.schemas.data_source import (
    ConnectionTestResult,
    DataConnectionCreate,
    DataConnectionOut,
    DataEntityOut,
    DataFieldOut,
    DataSourceCreate,
    DataSourceOut,
    DirectConnectionCreate,
    DiscoveryPayload,
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
    list_fields,
    record_connection_test_result,
    replace_discovery,
    test_direct_connection,
)

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
        return discover_direct_connection_schema(db, connection=connection)
    except Exception as exc:  # noqa: BLE001 — surfaced as a clean 400, never a raw driver error with the DSN in it
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not read the schema from this database. Test the connection first.") from exc


@router.get("/data-sources/{data_source_id}/entities", response_model=list[DataEntityOut])
def list_entities_route(data_source_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    source = _get_source_or_404(db, data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    return list_entities(db, data_source_id=data_source_id)


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
