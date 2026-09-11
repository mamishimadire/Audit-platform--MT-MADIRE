"""
Real, DB-backed regression tests for the maker-checker/SoD mechanics added
in this increment. Each test creates its own throwaway audit_test/control/
finding rows scoped to a disposable test organization, exercises the real
service function against the real database, and asserts the identity-based
guard fires — no mocking, since the whole point is that this logic is
correct against actual Postgres constraints and real foreign keys.

Run with: pytest tests/test_sod_workflows.py -v
"""
import uuid

import pytest

from sqlalchemy import select

from app.models.audit_test import AuditTest, TestDataMapping
from app.models.evidence_exception import Exception_
from app.models.monitoring import TestExecution
from app.models.organization import OrganizationSetting
from app.models.risk_control import Control
from app.schemas.audit_engine import TestRuleCreate
from app.schemas.control_binding import ControlTableBindingCreate
from app.schemas.finding import FindingCreate, RemediationActionCreate
from app.schemas.test_rule import ThresholdRule
from app.services import control_binding_service, control_service, exception_service, finding_service, mapping_service, test_rule_service
from app.services.execution_service import _READY_STATUSES
from app.services.tenant_scope_service import can_access_organization


def _make_audit_test(db, org_id):
    test = AuditTest(organization_id=org_id, test_name="SoD test", status="draft")
    db.add(test)
    db.commit()
    db.refresh(test)
    return test


def _rule_payload(name="Threshold rule"):
    return TestRuleCreate(
        rule_name=name,
        severity="high",
        rule_definition=ThresholdRule(object="transaction", field="amount", operator="gt", value=1000),
    )


# ---------------------------------------------------------------------------
# Test rules: maker-checker
# ---------------------------------------------------------------------------

def test_new_rule_starts_pending_approval_not_active(db, test_org, maker_user):
    audit_test = _make_audit_test(db, test_org.organization_id)
    rule = test_rule_service.create_test_rule(
        db, audit_test_id=audit_test.audit_test_id, payload=_rule_payload(),
        organization_id=test_org.organization_id, created_by_user_id=maker_user.user_id,
    )
    assert rule.status == "pending_approval"
    assert rule.approved_by is None


def test_rule_creator_cannot_approve_own_rule(db, test_org, maker_user):
    audit_test = _make_audit_test(db, test_org.organization_id)
    rule = test_rule_service.create_test_rule(
        db, audit_test_id=audit_test.audit_test_id, payload=_rule_payload(),
        organization_id=test_org.organization_id, created_by_user_id=maker_user.user_id,
    )
    with pytest.raises(ValueError, match="yourself"):
        test_rule_service.approve_rule(db, rule=rule, approved_by_user_id=maker_user.user_id, organization_id=test_org.organization_id)
    db.refresh(rule)
    assert rule.status == "pending_approval", "a rejected self-approval attempt must not have moved the rule to active"


def test_different_user_can_approve_rule(db, test_org, maker_user, checker_user):
    audit_test = _make_audit_test(db, test_org.organization_id)
    rule = test_rule_service.create_test_rule(
        db, audit_test_id=audit_test.audit_test_id, payload=_rule_payload(),
        organization_id=test_org.organization_id, created_by_user_id=maker_user.user_id,
    )
    approved = test_rule_service.approve_rule(db, rule=rule, approved_by_user_id=checker_user.user_id, organization_id=test_org.organization_id)
    assert approved.status == "active"
    assert approved.approved_by == checker_user.user_id


def test_editing_an_active_rule_sends_it_back_to_pending_approval(db, test_org, maker_user, checker_user):
    audit_test = _make_audit_test(db, test_org.organization_id)
    rule = test_rule_service.create_test_rule(
        db, audit_test_id=audit_test.audit_test_id, payload=_rule_payload(),
        organization_id=test_org.organization_id, created_by_user_id=maker_user.user_id,
    )
    test_rule_service.approve_rule(db, rule=rule, approved_by_user_id=checker_user.user_id, organization_id=test_org.organization_id)
    edited = test_rule_service.update_test_rule(
        db, rule=rule, payload=_rule_payload("Edited threshold rule"),
        organization_id=test_org.organization_id, updated_by_user_id=maker_user.user_id,
    )
    assert edited.status == "pending_approval", "an edit to an approved rule must require fresh approval, not keep running silently changed"
    assert edited.approved_by is None


# ---------------------------------------------------------------------------
# Control deactivation: mandatory dual control
# ---------------------------------------------------------------------------

def _make_active_control(db, org_id):
    control = Control(organization_id=org_id, control_name="SoD test control", status="active")
    db.add(control)
    db.commit()
    db.refresh(control)
    return control


def test_deactivation_requester_cannot_approve_own_request(db, test_org, maker_user):
    control = _make_active_control(db, test_org.organization_id)
    requested = control_service.request_deactivation(
        db, control=control, reason="No longer relevant", requested_by_user_id=maker_user.user_id
    )
    assert requested.status == "pending_deactivation"
    with pytest.raises(ValueError, match="yourself"):
        control_service.approve_deactivation(db, control=control, approved_by_user_id=maker_user.user_id)
    db.refresh(control)
    assert control.status == "pending_deactivation", "self-approval must not have deactivated the control"


def test_different_user_can_approve_deactivation(db, test_org, maker_user, checker_user):
    control = _make_active_control(db, test_org.organization_id)
    control_service.request_deactivation(db, control=control, reason="Superseded by AC-099", requested_by_user_id=maker_user.user_id)
    approved = control_service.approve_deactivation(db, control=control, approved_by_user_id=checker_user.user_id)
    assert approved.status == "inactive"
    assert approved.deactivation_approved_by == checker_user.user_id


def test_deactivation_request_requires_a_reason(db, test_org, maker_user):
    control = _make_active_control(db, test_org.organization_id)
    with pytest.raises(ValueError, match="reason"):
        control_service.request_deactivation(db, control=control, reason="   ", requested_by_user_id=maker_user.user_id)


# ---------------------------------------------------------------------------
# Remediation: performer cannot verify their own fix, when SoD is enabled
# ---------------------------------------------------------------------------

def _enable_sod(db, org_id):
    db.add(OrganizationSetting(organization_id=org_id, setting_name=exception_service.SOD_EXCEPTION_CLOSURE_SETTING, setting_value="true"))
    db.commit()


def _make_finding_with_remediation(db, org_id, *, completed_by):
    audit_test = _make_audit_test(db, org_id)
    execution = TestExecution(audit_test_id=audit_test.audit_test_id, status="completed")
    db.add(execution)
    db.flush()
    exception = Exception_(execution_id=execution.execution_id, exception_description="Test exception", status="open")
    db.add(exception)
    db.flush()
    finding = finding_service.create_finding(
        db, exception_id=exception.exception_id,
        payload=FindingCreate(finding_title="Test finding", risk_rating="high"),
        organization_id=org_id, created_by_user_id=completed_by,
    )
    action = finding_service.create_remediation_action(
        db, finding_id=finding.finding_id,
        payload=RemediationActionCreate(action_description="Fix it", responsible_user_id=completed_by),
        organization_id=org_id, created_by_user_id=completed_by,
    )
    finding_service.update_remediation_status(db, action=action, status="completed", organization_id=org_id, updated_by_user_id=completed_by)
    return audit_test, finding


def test_remediation_performer_cannot_verify_own_fix_when_sod_enabled(db, test_org, maker_user, checker_user):
    _enable_sod(db, test_org.organization_id)
    audit_test, finding = _make_finding_with_remediation(db, test_org.organization_id, completed_by=maker_user.user_id)
    with pytest.raises(ValueError, match="performed this remediation"):
        finding_service.create_retest(
            db, finding_id=finding.finding_id, audit_test_id=audit_test.audit_test_id, result="pass", comments=None,
            organization_id=test_org.organization_id, performed_by_user_id=maker_user.user_id,
        )


def test_different_user_can_verify_remediation(db, test_org, maker_user, checker_user):
    _enable_sod(db, test_org.organization_id)
    audit_test, finding = _make_finding_with_remediation(db, test_org.organization_id, completed_by=maker_user.user_id)
    retest = finding_service.create_retest(
        db, finding_id=finding.finding_id, audit_test_id=audit_test.audit_test_id, result="pass", comments=None,
        organization_id=test_org.organization_id, performed_by_user_id=checker_user.user_id,
    )
    assert retest.result == "pass"


def test_remediation_sod_off_allows_same_person_to_verify(db, test_org, maker_user):
    # SoD not enabled for this org (default) — same person completing and
    # verifying is the documented small-team fallback, not a bug.
    audit_test, finding = _make_finding_with_remediation(db, test_org.organization_id, completed_by=maker_user.user_id)
    retest = finding_service.create_retest(
        db, finding_id=finding.finding_id, audit_test_id=audit_test.audit_test_id, result="pass", comments=None,
        organization_id=test_org.organization_id, performed_by_user_id=maker_user.user_id,
    )
    assert retest.result == "pass"


# ---------------------------------------------------------------------------
# Exception self-closure (existing mechanism — regression guard)
# ---------------------------------------------------------------------------

def test_exception_owner_cannot_close_own_exception_when_sod_enabled(db, test_org, maker_user):
    _enable_sod(db, test_org.organization_id)
    audit_test = _make_audit_test(db, test_org.organization_id)
    execution = TestExecution(audit_test_id=audit_test.audit_test_id, status="completed")
    db.add(execution)
    db.flush()
    exception = Exception_(execution_id=execution.execution_id, exception_description="Owned exception", status="open", owner_id=maker_user.user_id)
    db.add(exception)
    db.commit()
    db.refresh(exception)

    with pytest.raises(ValueError, match="cannot also close it"):
        exception_service.update_exception(
            db, exception=exception, status="closed", owner_id=None,
            organization_id=test_org.organization_id, updated_by_user_id=maker_user.user_id,
        )


# ---------------------------------------------------------------------------
# Cross-tenant isolation (IDOR spot check)
# ---------------------------------------------------------------------------

def test_scoped_platform_user_cannot_access_a_different_organization(db, test_org, maker_user):
    from app.models.organization import Organization

    other_org = Organization(organization_name=f"Other org {uuid.uuid4().hex[:8]}", status="active")
    db.add(other_org)
    db.commit()
    db.refresh(other_org)
    try:
        assert can_access_organization(db, user=maker_user, organization_id=test_org.organization_id) is True
        assert can_access_organization(db, user=maker_user, organization_id=other_org.organization_id) is False
    finally:
        db.delete(other_org)
        db.commit()


# ---------------------------------------------------------------------------
# Data mapping: self-approval blocked, execution-readiness requires approval
# ---------------------------------------------------------------------------

def _make_data_entity(db, org_id):
    from app.models.data_source import DataEntity, DataSource

    source = DataSource(organization_id=org_id, source_name="SoD test source", source_type="postgresql", environment="cloud")
    db.add(source)
    db.flush()
    entity = DataEntity(data_source_id=source.data_source_id, entity_name="employees")
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return source, entity


def test_mapping_creator_cannot_approve_own_mapping(db, test_org, maker_user):
    audit_test = _make_audit_test(db, test_org.organization_id)
    source, entity = _make_data_entity(db, test_org.organization_id)
    mapping = TestDataMapping(
        audit_test_id=audit_test.audit_test_id,
        data_source_id=source.data_source_id,
        entity_id=entity.entity_id,
        canonical_field="employee.employee_id",
        confidence_score=95,
        mapping_status="auto",
        created_by=maker_user.user_id,
    )
    db.add(mapping)
    db.commit()
    db.refresh(mapping)

    with pytest.raises(ValueError, match="yourself"):
        mapping_service.approve_mapping(db, mapping=mapping, approved_by_user_id=maker_user.user_id, organization_id=test_org.organization_id)


def test_high_confidence_mapping_is_not_execution_ready_until_approved(db, test_org, maker_user, checker_user):
    """The exact gap flagged in the SoD review: a ≥90%-confidence mapping
    must not be execution-ready on confidence score alone — only an
    explicit approval makes it so."""
    audit_test = _make_audit_test(db, test_org.organization_id)
    source, entity = _make_data_entity(db, test_org.organization_id)
    mapping = TestDataMapping(
        audit_test_id=audit_test.audit_test_id,
        data_source_id=source.data_source_id,
        entity_id=entity.entity_id,
        canonical_field="employee.employee_id",
        confidence_score=99,
        mapping_status="auto",  # what create_mapping sets for a >=90% suggestion
        created_by=maker_user.user_id,
    )
    db.add(mapping)
    db.commit()
    db.refresh(mapping)

    def is_ready():
        row = db.scalar(
            select(TestDataMapping).where(TestDataMapping.mapping_id == mapping.mapping_id, TestDataMapping.mapping_status.in_(_READY_STATUSES))
        )
        return row is not None

    assert is_ready() is False, "a 99%-confidence, never-approved mapping must not be execution-ready"
    mapping_service.approve_mapping(db, mapping=mapping, approved_by_user_id=checker_user.user_id, organization_id=test_org.organization_id)
    assert is_ready() is True, "once explicitly approved by a different user, it becomes execution-ready"


# ---------------------------------------------------------------------------
# Table binding changes on an active control are blocked (must deactivate first)
# ---------------------------------------------------------------------------

def test_cannot_rebind_a_table_on_an_active_control(db, test_org, maker_user):
    control = _make_active_control(db, test_org.organization_id)
    with pytest.raises(ValueError, match="active"):
        control_binding_service.bind_table(
            db, control=control,
            payload=ControlTableBindingCreate(canonical_table_name="system_users", data_source_id=uuid.uuid4(), entity_id=uuid.uuid4()),
            bound_by_user_id=maker_user.user_id,
        )


def test_cannot_unbind_a_table_on_an_active_control(db, test_org, maker_user):
    control = _make_active_control(db, test_org.organization_id)
    with pytest.raises(ValueError, match="active"):
        control_binding_service.unbind_table(db, control=control, canonical_table_name="system_users", unbound_by_user_id=maker_user.user_id)


# ---------------------------------------------------------------------------
# Immutable versioning: editing an active/approved item creates a new
# version instead of overwriting live, already-reviewed content in place.
# ---------------------------------------------------------------------------

def test_editing_an_active_rule_creates_a_new_version_not_an_overwrite(db, test_org, maker_user, checker_user):
    audit_test = _make_audit_test(db, test_org.organization_id)
    rule = test_rule_service.create_test_rule(
        db, audit_test_id=audit_test.audit_test_id, payload=_rule_payload(),
        organization_id=test_org.organization_id, created_by_user_id=maker_user.user_id,
    )
    approved = test_rule_service.approve_rule(db, rule=rule, approved_by_user_id=checker_user.user_id, organization_id=test_org.organization_id)
    original_rule_id = approved.rule_id
    assert approved.version == 1

    edited = test_rule_service.update_test_rule(
        db, rule=approved, payload=_rule_payload("Edited threshold rule"),
        organization_id=test_org.organization_id, updated_by_user_id=maker_user.user_id,
    )

    assert edited.rule_id != original_rule_id, "editing an active rule must create a new row, not reuse the approved one's id"
    assert edited.version == 2
    assert edited.supersedes_rule_id == original_rule_id
    assert edited.status == "pending_approval"

    db.refresh(approved)
    assert approved.status == "superseded", "the original approved version must survive, marked superseded, not be overwritten"
    assert approved.rule_name != "Edited threshold rule", "the superseded row's own content must be untouched"

    visible = test_rule_service.list_test_rules(db, audit_test_id=audit_test.audit_test_id)
    visible_ids = {r.rule_id for r in visible}
    assert edited.rule_id in visible_ids
    assert original_rule_id not in visible_ids, "list_test_rules should show the current version, not superseded history"


def test_editing_a_pending_rule_edits_in_place(db, test_org, maker_user):
    audit_test = _make_audit_test(db, test_org.organization_id)
    rule = test_rule_service.create_test_rule(
        db, audit_test_id=audit_test.audit_test_id, payload=_rule_payload(),
        organization_id=test_org.organization_id, created_by_user_id=maker_user.user_id,
    )
    edited = test_rule_service.update_test_rule(
        db, rule=rule, payload=_rule_payload("Edited before approval"),
        organization_id=test_org.organization_id, updated_by_user_id=maker_user.user_id,
    )
    assert edited.rule_id == rule.rule_id, "a never-approved rule has nothing live to protect — editing in place is fine"
    assert edited.version == 1


def test_editing_an_approved_mapping_creates_a_new_version(db, test_org, maker_user, checker_user):
    audit_test = _make_audit_test(db, test_org.organization_id)
    source, entity = _make_data_entity(db, test_org.organization_id)
    mapping = TestDataMapping(
        audit_test_id=audit_test.audit_test_id,
        data_source_id=source.data_source_id,
        entity_id=entity.entity_id,
        canonical_field="employee.employee_id",
        confidence_score=95,
        mapping_status="auto",
        created_by=maker_user.user_id,
    )
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    approved = mapping_service.approve_mapping(db, mapping=mapping, approved_by_user_id=checker_user.user_id, organization_id=test_org.organization_id)
    original_id = approved.mapping_id

    edited = mapping_service.update_mapping_field(db, mapping=approved, canonical_field="employee.termination_date", updated_by_user_id=maker_user.user_id)

    assert edited.mapping_id != original_id, "editing an approved mapping must create a new row"
    assert edited.version == 2
    assert edited.supersedes_mapping_id == original_id
    assert edited.mapping_status == "manually_mapped"

    db.refresh(approved)
    assert approved.mapping_status == "superseded"
    assert approved.canonical_field == "employee.employee_id", "the superseded row's own data must be untouched"

    current = mapping_service.list_mappings(db, audit_test_id=audit_test.audit_test_id)
    assert original_id not in {m.mapping_id for m in current}
    assert edited.mapping_id in {m.mapping_id for m in current}

    history = mapping_service.list_mapping_history(db, audit_test_id=audit_test.audit_test_id)
    assert {m.mapping_id for m in history} == {original_id, edited.mapping_id}, "history must still contain both versions"
