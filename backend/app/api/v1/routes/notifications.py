import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user
from app.db.session import get_db
from app.models.rbac import User
from app.schemas.notification import NotificationDismissRequest, PendingApprovalOut
from app.services.notification_service import (
    clear_all_notifications,
    dismiss_notification,
    list_notification_history,
    list_pending_approvals,
)

router = APIRouter(tags=["notifications"])


@router.get("/organizations/{organization_id}/pending-approvals", response_model=list[PendingApprovalOut])
def pending_approvals(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[PendingApprovalOut]:
    enforce_same_organization(organization_id, user, db)
    return list_pending_approvals(db, organization_id=organization_id, user=user)


@router.get("/organizations/{organization_id}/notifications/history", response_model=list[PendingApprovalOut])
def notification_history(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[PendingApprovalOut]:
    enforce_same_organization(organization_id, user, db)
    return list_notification_history(db, user_id=user.user_id)


@router.post("/organizations/{organization_id}/notifications/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_one_notification(
    organization_id: uuid.UUID,
    payload: NotificationDismissRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    enforce_same_organization(organization_id, user, db)
    dismiss_notification(db, user_id=user.user_id, item=payload)


@router.post("/organizations/{organization_id}/notifications/clear-all", status_code=status.HTTP_204_NO_CONTENT)
def clear_all(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> None:
    enforce_same_organization(organization_id, user, db)
    clear_all_notifications(db, organization_id=organization_id, user=user)
