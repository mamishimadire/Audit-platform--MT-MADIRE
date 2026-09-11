from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.rbac import User
from app.schemas.control_library import ControlLibraryOut
from app.services.control_library_service import list_control_library

router = APIRouter(prefix="/control-library", tags=["control-library"])


@router.get("", response_model=list[ControlLibraryOut])
def list_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[ControlLibraryOut]:
    # Global reference data — same list for every organization, so this is
    # authenticated-only, not organization-scoped.
    return list(list_control_library(db))
