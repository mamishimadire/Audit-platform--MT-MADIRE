import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_test import AuditTest, TestDataMapping, TestRule
from app.models.data_source import DataConnection, DataEntity, DataField
from app.models.evidence_exception import Evidence, Exception_, ExceptionRecord
from app.models.finding import Finding
from app.services.exception_service import explain_exception, find_open_exception
from app.models.monitoring import MonitoringSchedule, TestExecution
from app.schemas.audit_engine import DueTest, DueTestObject, ExecutionReport
from app.schemas.test_rule import required_objects_for
from app.services.audit_log_service import log_action
from app.services.monitoring_service import next_run_after
from app.services.rule_parameter_service import get_parameters, resolve_parameters
from app.core.security import fingerprint

# Only an explicitly reviewed-and-approved mapping is execution-ready.
# 'auto' (≥90% AI confidence) and 'manually_mapped' used to be treated as
# ready too — that let a mapping run in production without any human ever
# reviewing it, purely because a scoring heuristic liked it. Confidence is
# a triage aid for the mapping screen, never a substitute for approval.
_READY_STATUSES = {"approved"}


def resolve_due_tests_for_gateway(db: Session, *, gateway_id: uuid.UUID) -> list[DueTest]:
    """
    Everything a Gateway needs to execute its assigned audit tests locally,
    without ever giving the platform direct access to the client's database
    (that access never leaves the Gateway's own machine). A test is "due"
    for THIS gateway only if every canonical object its rule references
    resolves to a connection this gateway owns — cross-gateway joins are a
    real limitation, not silently attempted.
    """
    now = datetime.now(timezone.utc)
    connection_rows = list(db.execute(select(DataConnection.connection_id, DataConnection.data_source_id).where(DataConnection.gateway_id == gateway_id)))
    if not connection_rows:
        return []
    connection_by_source = {row.data_source_id: row.connection_id for row in connection_rows}

    due: list[DueTest] = []
    parameters_by_org: dict[uuid.UUID, dict[str, float]] = {}

    schedules = db.scalars(
        select(MonitoringSchedule).where(
            MonitoringSchedule.is_active.is_(True),
            (MonitoringSchedule.next_run.is_(None)) | (MonitoringSchedule.next_run <= now),
        )
    )
    for schedule in schedules:
        rule = db.scalar(
            select(TestRule).where(TestRule.audit_test_id == schedule.audit_test_id, TestRule.status == "active")
        )
        if rule is None:
            continue
        rule_definition = json.loads(rule.rule_definition)
        audit_test = db.get(AuditTest, schedule.audit_test_id)
        if audit_test is None:
            continue
        if audit_test.organization_id not in parameters_by_org:
            parameters_by_org[audit_test.organization_id] = get_parameters(db, organization_id=audit_test.organization_id)
        rule_definition = resolve_parameters(rule_definition, parameters_by_org[audit_test.organization_id])
        try:
            needed_objects = required_objects_for(rule_definition)
        except Exception:  # noqa: BLE001 — a malformed rule must not crash the whole due-tests pull
            continue
        if not needed_objects:
            continue

        mappings = db.scalars(
            select(TestDataMapping).where(
                TestDataMapping.audit_test_id == schedule.audit_test_id, TestDataMapping.mapping_status.in_(_READY_STATUSES)
            )
        )

        objects: dict[str, DueTestObject] = {}
        for mapping in mappings:
            if not mapping.canonical_field or "." not in mapping.canonical_field:
                continue
            canonical_object, canonical_field = mapping.canonical_field.split(".", 1)
            connection_id = connection_by_source.get(mapping.data_source_id)
            if connection_id is None:
                continue  # this mapping's data source isn't reachable by this gateway
            entity = db.get(DataEntity, mapping.entity_id)
            field = db.get(DataField, mapping.field_id) if mapping.field_id else None
            if entity is None or field is None:
                continue

            if canonical_object not in objects:
                objects[canonical_object] = DueTestObject(connection_id=connection_id, entity_name=entity.entity_name, fields={})
            elif objects[canonical_object].connection_id != connection_id or objects[canonical_object].entity_name != entity.entity_name:
                continue  # conflicting mappings for the same object — skip rather than guess
            objects[canonical_object].fields[canonical_field] = field.field_name

        if not needed_objects.issubset(objects.keys()):
            continue  # not fully mapped/reachable by this gateway yet

        due.append(
            DueTest(
                audit_test_id=schedule.audit_test_id,
                schedule_id=schedule.schedule_id,
                rule_id=rule.rule_id,
                rule_definition=rule_definition,
                objects=objects,
            )
        )

    return due


def record_execution_report(db: Session, *, report: ExecutionReport) -> TestExecution:
    """
    Records exactly what the Gateway reported — a failed test is recorded as
    FAILED, never silently reinterpreted as a pass (Section 31: never
    classify a failed test as a successful control).
    """
    audit_test = db.get(AuditTest, report.audit_test_id)
    organization_id = audit_test.organization_id if audit_test else None

    execution = TestExecution(
        audit_test_id=report.audit_test_id,
        rule_id=report.rule_id,
        started_at=report.started_at,
        completed_at=report.completed_at,
        status=report.status,
        records_analyzed=report.records_analyzed,
        exceptions_found=len(report.exceptions),
        execution_log=report.error_message,
    )
    db.add(execution)
    db.flush()

    evidence_summary = json.dumps(
        {
            "audit_test_id": str(report.audit_test_id),
            "records_analyzed": report.records_analyzed,
            "exceptions_found": len(report.exceptions),
            "started_at": report.started_at.isoformat(),
            "completed_at": report.completed_at.isoformat(),
        },
        sort_keys=True,
    )
    db.add(
        Evidence(
            execution_id=execution.execution_id,
            evidence_type="test_result",
            # No object storage exists yet (Phase 9+) — the summary is stored
            # inline rather than pretending a file was written somewhere.
            evidence_location=evidence_summary,
            evidence_hash=fingerprint(evidence_summary),
        )
    )

    now = datetime.now(timezone.utc)
    for exc in report.exceptions:
        full_description = f"{audit_test.test_name if audit_test else 'Audit test'}: exception on {exc.record_identifier}"
        existing = find_open_exception(db, audit_test_id=report.audit_test_id, description=full_description)

        if existing is not None:
            existing.last_detected_at = now
            existing.occurrence_count += 1
            # Re-point at the run that just re-detected it, not the run
            # that first created it — otherwise a recurring exception
            # (the normal case for anything not yet fixed) permanently
            # joins to a stale, long-past execution, and the Executions
            # page's per-row "show full explanation" (which matches
            # exceptions to executions by execution_id) never finds it for
            # any execution after the very first one. detected_at (below,
            # left untouched) still preserves when this was FIRST seen.
            existing.execution_id = execution.execution_id
            exception_id = existing.exception_id
        else:
            exception_row = Exception_(
                execution_id=execution.execution_id,
                exception_description=full_description,
                severity="medium",
                status="open",
                last_detected_at=now,
            )
            db.add(exception_row)
            db.flush()
            exception_id = exception_row.exception_id

        db.add(
            ExceptionRecord(
                exception_id=exception_id,
                record_identifier=exc.record_identifier,
                exception_data=exc.exception_data,
            )
        )

    schedule = db.get(MonitoringSchedule, report.schedule_id)
    if schedule is not None:
        schedule.last_run = report.completed_at
        schedule.next_run = next_run_after(schedule.frequency, report.completed_at)

    log_action(
        db,
        action=f"Audit test executed: {report.status}",
        organization_id=organization_id,
        entity_type="test_executions",
        entity_id=execution.execution_id,
        new_value={"status": report.status, "exceptions_found": len(report.exceptions)},
    )

    db.commit()
    db.refresh(execution)
    return execution


def list_executions(db: Session, *, audit_test_id: uuid.UUID) -> list[TestExecution]:
    return list(db.scalars(select(TestExecution).where(TestExecution.audit_test_id == audit_test_id)))


def list_executions_for_organization(db: Session, *, organization_id: uuid.UUID) -> list[TestExecution]:
    return list(
        db.scalars(
            select(TestExecution)
            .join(AuditTest, AuditTest.audit_test_id == TestExecution.audit_test_id)
            .where(AuditTest.organization_id == organization_id)
        )
    )


def list_evidence_for_organization(db: Session, *, organization_id: uuid.UUID) -> list[Evidence]:
    return list(
        db.scalars(
            select(Evidence)
            .join(TestExecution, TestExecution.execution_id == Evidence.execution_id)
            .join(AuditTest, AuditTest.audit_test_id == TestExecution.audit_test_id)
            .where(AuditTest.organization_id == organization_id)
        )
    )


def list_exceptions_for_organization(db: Session, *, organization_id: uuid.UUID) -> list[Exception_]:
    exceptions = list(
        db.scalars(
            select(Exception_)
            .join(TestExecution, TestExecution.execution_id == Exception_.execution_id)
            .join(AuditTest, AuditTest.audit_test_id == TestExecution.audit_test_id)
            .where(AuditTest.organization_id == organization_id)
        )
    )
    if not exceptions:
        return exceptions

    exception_ids = [exc.exception_id for exc in exceptions]
    ids_with_findings = set(
        db.scalars(select(Finding.exception_id).where(Finding.exception_id.in_(exception_ids)))
    )
    for exc in exceptions:
        exc.has_finding = exc.exception_id in ids_with_findings
        # Attached here (not just the dedicated .../explanation endpoint) so
        # every screen that lists exceptions in bulk — Exceptions, and
        # Executions' per-row reasons — shows the same plain-English
        # explanation without a separate fetch per row.
        explained = explain_exception(db, exception=exc)
        exc.summary = explained["summary"]
        exc.why_it_matters = explained["why_it_matters"]
        exc.what_to_do = explained["what_to_do"]
    return exceptions
