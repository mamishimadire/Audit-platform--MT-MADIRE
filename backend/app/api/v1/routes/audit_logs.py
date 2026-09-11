import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.rbac import User
from app.schemas.audit_log import AuditLogOut

router = APIRouter(tags=["audit-logs"])


@router.get("/organizations/{organization_id}/audit-logs", response_model=list[AuditLogOut])
def list_all(
    organization_id: uuid.UUID,
    limit: int = 200,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_log:read")),
) -> list[AuditLog]:
    enforce_same_organization(organization_id, user, db)
    stmt = (
        select(AuditLog)
        .where(AuditLog.organization_id == organization_id)
        .order_by(AuditLog.timestamp.desc())
        .limit(min(limit, 500))
    )
    return list(db.scalars(stmt))
