import uuid

from app.schemas.common import OrmModel


class AuditTestOut(OrmModel):
    audit_test_id: uuid.UUID
    organization_id: uuid.UUID
    test_code: str | None
    test_name: str
    test_description: str | None
    test_type: str | None
    frequency: str | None
    status: str
    control_ids: list[uuid.UUID] = []
    domain: str | None = None
    required_tables: list[str] = []
