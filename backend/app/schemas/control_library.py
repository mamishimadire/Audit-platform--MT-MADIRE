import uuid

from app.schemas.common import OrmModel


class ControlLibraryOut(OrmModel):
    control_library_id: uuid.UUID
    domain: str
    control_code: str
    control_name: str
    audit_procedure: str
    required_tables: list[str]
    default_control_type: str | None
    default_control_nature: str | None
    default_control_frequency: str | None
