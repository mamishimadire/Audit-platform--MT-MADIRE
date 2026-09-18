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
    # "not_mapped" / "pending_approval" / "rejected" / "approved" — see
    # mapping_service.get_mapping_status_for_tests. Separate from `status`
    # above, which is this test's own lifecycle state (draft/active/...),
    # not its data mapping's approval state.
    mapping_status: str = "not_mapped"
