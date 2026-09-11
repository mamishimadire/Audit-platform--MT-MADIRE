import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_gateway, get_current_user, require_permissions
from app.db.session import get_db
from app.models.data_source import Gateway
from app.models.rbac import User
from app.schemas.gateway import (
    GatewayCreate,
    GatewayCreatedOut,
    GatewayHeartbeat,
    GatewayOut,
    GatewayRegisterRequest,
    GatewayRegisterResponse,
)
from app.services.gateway_download_service import build_gateway_archive
from app.services.gateway_service import (
    create_gateway,
    list_gateways,
    record_heartbeat,
    redeem_registration_code,
    revoke_gateway,
)

router = APIRouter(tags=["gateways"])


@router.get("/gateways/download/{platform}")
def download(platform: str) -> Response:
    """
    Public by design (no auth) — this ships generic installer source, not a
    credential. The registration code (redeemed via POST /gateways/register)
    is what actually binds an installed Gateway to a tenant.
    """
    try:
        content, filename, media_type = build_gateway_archive(platform)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The Gateway application isn't available for download right now — please try again shortly.",
        ) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/organizations/{organization_id}/gateways", response_model=GatewayCreatedOut, status_code=status.HTTP_201_CREATED)
def create(
    organization_id: uuid.UUID,
    payload: GatewayCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("data_sources:manage")),
) -> Gateway:
    enforce_same_organization(organization_id, user, db)
    return create_gateway(db, organization_id=organization_id, payload=payload, created_by_user_id=user.user_id)


@router.get("/organizations/{organization_id}/gateways", response_model=list[GatewayOut])
def list_all(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[Gateway]:
    enforce_same_organization(organization_id, user, db)
    return list_gateways(db, organization_id=organization_id)


@router.post("/gateways/register", response_model=GatewayRegisterResponse)
def register(payload: GatewayRegisterRequest, db: Session = Depends(get_db)):
    """
    Called by the Gateway process itself during install — no user session
    exists yet, only the registration code. This is the sole unauthenticated
    endpoint in the gateway API surface, and it only ever accepts a
    single-use, time-limited, tenant-bound code (Section 5 of the spec).
    """
    try:
        gateway, api_key = redeem_registration_code(
            db, registration_code=payload.registration_code, gateway_name=payload.gateway_name, version=payload.version
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return GatewayRegisterResponse(gateway_id=gateway.gateway_id, organization_id=gateway.organization_id, api_key=api_key)


@router.post("/gateways/{gateway_id}/heartbeat", response_model=GatewayOut)
def heartbeat(
    gateway_id: uuid.UUID, payload: GatewayHeartbeat, db: Session = Depends(get_db),
    gateway: Gateway = Depends(get_current_gateway),
) -> Gateway:
    if gateway.gateway_id != gateway_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Gateway credentials do not match this gateway")
    return record_heartbeat(db, gateway=gateway, version=payload.version)


@router.post("/gateways/{gateway_id}/revoke", response_model=GatewayOut)
def revoke(
    gateway_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_permissions("data_sources:manage"))
) -> Gateway:
    gateway = db.get(Gateway, gateway_id)
    if gateway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    enforce_same_organization(gateway.organization_id, user, db)
    return revoke_gateway(db, gateway=gateway, revoked_by_user_id=user.user_id)
