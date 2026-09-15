import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user
from app.db.session import get_db
from app.models.rbac import User
from app.schemas.notification import PendingApprovalOut
from app.services.notification_service import list_pending_approvals

router = APIRouter(tags=["notifications"])


@router.get("/organizations/{organization_id}/pending-approvals", response_model=list[PendingApprovalOut])
def pending_approvals(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[PendingApprovalOut]:
    enforce_same_organization(organization_id, user, db)
    return list_pending_approvals(db, organization_id=organization_id, user=user)
