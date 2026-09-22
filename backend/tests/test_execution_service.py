"""
record_execution_report against the real database (see conftest's `db`/
`test_org` fixtures — an organization's cascade delete cleans up everything
this file creates: audit_tests -> test_executions -> exceptions ->
exception_records, all ON DELETE CASCADE).

Covers the auto-resolve behaviour added alongside the fix for API-002's
stale U012 exception: a previously open exception that a later, genuine
run of the same audit test does not reproduce is closed automatically,
the same way device_compliance_service already closes a device check that
starts passing again.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.execution_status import EXCEPTION, INSUFFICIENT_DATA, PASS
from app.models.audit_test import AuditTest
from app.models.evidence_exception import Exception_
from app.schemas.audit_engine import ExceptionReport, ExecutionReport
from app.services.execution_service import record_execution_report


def _make_audit_test(db, org_id):
    test = AuditTest(organization_id=org_id, test_name="Exception lifecycle test", status="active")
    db.add(test)
    db.commit()
    db.refresh(test)
    return test


def _report(audit_test_id, *, status, record_ids):
    now = datetime.now(timezone.utc)
    return ExecutionReport(
        audit_test_id=audit_test_id,
        schedule_id=uuid.uuid4(),  # no MonitoringSchedule row — record_execution_report's schedule update is a no-op then
        rule_id=None,  # no real TestRule row needed for this — rule_id is optional and FK-checked when set
        started_at=now,
        completed_at=now,
        status=status,
        records_analyzed=len(record_ids) or 1,
        exceptions=[ExceptionReport(record_identifier=r, exception_data={"id": r}) for r in record_ids],
    )


def _open_exceptions(db, audit_test_id):
    from app.models.monitoring import TestExecution

    return list(
        db.scalars(
            select(Exception_)
            .join(TestExecution, TestExecution.execution_id == Exception_.execution_id)
            .where(TestExecution.audit_test_id == audit_test_id)
        )
    )


def test_a_fresh_exception_is_created_open(db, test_org):
    test = _make_audit_test(db, test_org.organization_id)
    record_execution_report(db, report=_report(test.audit_test_id, status=EXCEPTION, record_ids=["U012"]))
    rows = _open_exceptions(db, test.audit_test_id)
    assert len(rows) == 1
    assert rows[0].status == "open"
    assert rows[0].occurrence_count == 1


def test_a_recurring_exception_bumps_occurrence_count_not_a_new_row(db, test_org):
    test = _make_audit_test(db, test_org.organization_id)
    record_execution_report(db, report=_report(test.audit_test_id, status=EXCEPTION, record_ids=["U012"]))
    record_execution_report(db, report=_report(test.audit_test_id, status=EXCEPTION, record_ids=["U012"]))
    rows = _open_exceptions(db, test.audit_test_id)
    assert len(rows) == 1
    assert rows[0].occurrence_count == 2


def test_an_exception_no_longer_reproduced_is_auto_resolved(db, test_org):
    """The exact shape of the live API-002/U012 defect: a run that used to
    flag something, then a later, genuine run of the same test that no
    longer finds it, must close it — not leave it open forever."""
    test = _make_audit_test(db, test_org.organization_id)
    record_execution_report(db, report=_report(test.audit_test_id, status=EXCEPTION, record_ids=["U012"]))
    record_execution_report(db, report=_report(test.audit_test_id, status=PASS, record_ids=[]))
    rows = _open_exceptions(db, test.audit_test_id)
    assert len(rows) == 1
    assert rows[0].status == "resolved"


def test_only_the_no_longer_reproduced_one_is_resolved_not_every_open_exception(db, test_org):
    test = _make_audit_test(db, test_org.organization_id)
    record_execution_report(db, report=_report(test.audit_test_id, status=EXCEPTION, record_ids=["U012", "U099"]))
    # A later run still finds U012, but not U099 — only U099 should close.
    record_execution_report(db, report=_report(test.audit_test_id, status=EXCEPTION, record_ids=["U012"]))
    rows = {r.exception_description: r.status for r in _open_exceptions(db, test.audit_test_id)}
    assert rows[f"{test.test_name}: exception on U012"] == "open"
    assert rows[f"{test.test_name}: exception on U099"] == "resolved"


def test_insufficient_data_never_auto_resolves_anything(db, test_org):
    """Zero records checked proves nothing was confirmed absent — closing on
    an INSUFFICIENT_DATA run would silently hide a real, unverified issue
    behind what looks like a clean re-test."""
    test = _make_audit_test(db, test_org.organization_id)
    record_execution_report(db, report=_report(test.audit_test_id, status=EXCEPTION, record_ids=["U012"]))
    stale_report = _report(test.audit_test_id, status=INSUFFICIENT_DATA, record_ids=[])
    stale_report.records_analyzed = 0
    record_execution_report(db, report=stale_report)
    rows = _open_exceptions(db, test.audit_test_id)
    assert len(rows) == 1
    assert rows[0].status == "open", "an unverified run must never auto-resolve an existing exception"


def test_an_already_resolved_exception_is_left_alone_and_a_recurrence_reopens_as_new(db, test_org):
    """A regression after resolution is a genuinely new exception, not a
    silent re-open of the old (already-reviewed) record — same design as
    find_open_exception's own docstring."""
    test = _make_audit_test(db, test_org.organization_id)
    record_execution_report(db, report=_report(test.audit_test_id, status=EXCEPTION, record_ids=["U012"]))
    record_execution_report(db, report=_report(test.audit_test_id, status=PASS, record_ids=[]))
    resolved = _open_exceptions(db, test.audit_test_id)
    assert len(resolved) == 1 and resolved[0].status == "resolved"

    record_execution_report(db, report=_report(test.audit_test_id, status=EXCEPTION, record_ids=["U012"]))
    all_rows = _open_exceptions(db, test.audit_test_id)
    assert len(all_rows) == 2, "the resolved row stays, plus a fresh open one for the regression"
    statuses = sorted(r.status for r in all_rows)
    assert statuses == ["open", "resolved"]
