from app.models.audit_test import AuditTest
from app.models.evidence_exception import Exception_
from app.models.monitoring import TestExecution
from app.services.device_compliance_service import COMPLIANCE_TEST_CODE
from app.services.device_policy_service import DEFAULT_POLICY, get_device_policy, set_device_policy
from app.services.software_compliance_service import SOFTWARE_TEST_CODE


def _make_open_exception(db, *, org_id, test_code, description):
    test = AuditTest(organization_id=org_id, test_name=test_code, test_code=test_code, status="active")
    db.add(test)
    db.flush()
    execution = TestExecution(audit_test_id=test.audit_test_id, status="failed")
    db.add(execution)
    db.flush()
    exc = Exception_(execution_id=execution.execution_id, exception_description=description, status="open", severity="high")
    db.add(exc)
    db.commit()
    db.refresh(exc)
    return exc


def test_disabling_a_check_resolves_only_its_own_open_exceptions(db, test_org, maker_user):
    disk_exc = _make_open_exception(
        db, org_id=test_org.organization_id, test_code=COMPLIANCE_TEST_CODE,
        description="joy: Disk encryption (BitLocker) is not enabled",
    )
    antivirus_exc = _make_open_exception(
        db, org_id=test_org.organization_id, test_code=COMPLIANCE_TEST_CODE,
        description="joy: Antivirus/real-time protection is disabled",
    )

    new_policy = {**DEFAULT_POLICY, "require_disk_encryption": False}
    set_device_policy(db, organization_id=test_org.organization_id, policy=new_policy, updated_by_user_id=maker_user.user_id)

    db.refresh(disk_exc)
    db.refresh(antivirus_exc)
    assert disk_exc.status == "resolved"
    assert antivirus_exc.status == "open"  # a different, still-enabled check must not be touched


def test_disabling_software_compliance_resolves_all_its_open_exceptions(db, test_org, maker_user):
    exc1 = _make_open_exception(db, org_id=test_org.organization_id, test_code=SOFTWARE_TEST_CODE, description="joy: Restricted application installed: FL Studio 21")
    exc2 = _make_open_exception(db, org_id=test_org.organization_id, test_code=SOFTWARE_TEST_CODE, description="Finance: Restricted application installed: FL Studio 20")

    new_policy = {**DEFAULT_POLICY, "require_software_compliance": False}
    set_device_policy(db, organization_id=test_org.organization_id, policy=new_policy, updated_by_user_id=maker_user.user_id)

    db.refresh(exc1)
    db.refresh(exc2)
    assert exc1.status == "resolved"
    assert exc2.status == "resolved"


def test_leaving_a_check_enabled_does_not_touch_its_exceptions(db, test_org, maker_user):
    exc = _make_open_exception(
        db, org_id=test_org.organization_id, test_code=COMPLIANCE_TEST_CODE,
        description="joy: Disk encryption (BitLocker) is not enabled",
    )
    # Toggle an unrelated check, disk encryption stays enabled throughout.
    policy = get_device_policy(db, organization_id=test_org.organization_id)
    set_device_policy(db, organization_id=test_org.organization_id, policy={**policy, "require_antivirus": False}, updated_by_user_id=maker_user.user_id)
    db.refresh(exc)
    assert exc.status == "open"


def test_re_enabling_a_check_does_not_resolve_anything(db, test_org, maker_user):
    exc = _make_open_exception(
        db, org_id=test_org.organization_id, test_code=COMPLIANCE_TEST_CODE,
        description="joy: Disk encryption (BitLocker) is not enabled",
    )
    set_device_policy(db, organization_id=test_org.organization_id, policy={**DEFAULT_POLICY, "require_disk_encryption": False}, updated_by_user_id=maker_user.user_id)
    db.refresh(exc)
    assert exc.status == "resolved"

    # Manually reopen to prove re-enabling the check doesn't touch it again.
    exc.status = "open"
    db.commit()
    set_device_policy(db, organization_id=test_org.organization_id, policy={**DEFAULT_POLICY, "require_disk_encryption": True}, updated_by_user_id=maker_user.user_id)
    db.refresh(exc)
    assert exc.status == "open"
