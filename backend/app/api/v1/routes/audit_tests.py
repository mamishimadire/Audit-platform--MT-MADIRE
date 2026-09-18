import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user
from app.db.session import get_db
from app.models.rbac import User
from app.schemas.audit_test import AuditTestOut
from app.services.audit_test_service import describe_tests, list_audit_tests
from app.services.mapping_service import get_mapping_status_for_tests

router = APIRouter(prefix="/organizations/{organization_id}/audit-tests", tags=["audit-tests"])


@router.get("", response_model=list[AuditTestOut])
def list_all(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[AuditTestOut]:
    enforce_same_organization(organization_id, user, db)
    tests = list_audit_tests(db, organization_id=organization_id)
    test_ids = [t.audit_test_id for t in tests]
    described = describe_tests(db, audit_test_ids=test_ids)
    mapping_statuses = get_mapping_status_for_tests(db, audit_test_ids=test_ids)
    out = []
    for test in tests:
        control_ids, domain, required_tables = described.get(test.audit_test_id, ([], None, []))
        row = AuditTestOut.model_validate(test)
        out.append(
            row.model_copy(
                update={
                    "control_ids": control_ids,
                    "domain": domain,
                    "required_tables": required_tables,
                    "mapping_status": mapping_statuses.get(test.audit_test_id, "not_mapped"),
                }
            )
        )
    return out
