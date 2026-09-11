from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.control_library import ControlLibraryEntry


def list_control_library(db: Session) -> list[ControlLibraryEntry]:
    return list(db.scalars(select(ControlLibraryEntry).order_by(ControlLibraryEntry.domain, ControlLibraryEntry.control_code)))
