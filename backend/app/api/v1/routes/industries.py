from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.rbac import User
from app.schemas.industry import IndustryOut
from app.services.industry_service import list_industries

router = APIRouter(prefix="/reference", tags=["reference"])


@router.get("/industries", response_model=list[IndustryOut])
def list_all(db: Session = Depends(get_db), _user: User = Depends(get_current_user)) -> list[IndustryOut]:
    return list(list_industries(db))
