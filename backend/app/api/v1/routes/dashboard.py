import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user
from app.db.session import get_db
from app.models.rbac import User
from app.schemas.dashboard import DashboardStats
from app.services.dashboard_service import get_dashboard_stats

router = APIRouter(tags=["dashboard"])


@router.get("/organizations/{organization_id}/dashboard", response_model=DashboardStats)
def dashboard(organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> DashboardStats:
    enforce_same_organization(organization_id, user, db)
    return get_dashboard_stats(db, organization_id=organization_id)
