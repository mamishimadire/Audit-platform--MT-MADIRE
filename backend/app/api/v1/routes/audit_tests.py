import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user
from app.db.session import get_db
from app.models.audit_test import AuditTest
from app.models.rbac import User
from app.schemas.audit_test import AuditTestOut
from app.services.audit_test_service import get_control_ids_for_test, get_domain_and_tables_for_test, list_audit_tests

router = APIRouter(prefix="/organizations/{organization_id}/audit-tests", tags=["audit-tests"])


def _to_out(db: Session, test: AuditTest) -> AuditTestOut:
    out = AuditTestOut.model_validate(test)
    control_ids = get_control_ids_for_test(db, audit_test_id=test.audit_test_id)
    domain, required_tables = get_domain_and_tables_for_test(db, control_ids=control_ids)
    return out.model_copy(update={"control_ids": control_ids, "domain": domain, "required_tables": required_tables})


@router.get("", response_model=list[AuditTestOut])
def list_all(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[AuditTestOut]:
    enforce_same_organization(organization_id, user, db)
    return [_to_out(db, t) for t in list_audit_tests(db, organization_id=organization_id)]
