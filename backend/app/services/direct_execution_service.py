"""
Executes due audit tests for direct (non-Gateway) connections — MongoDB
Atlas, or any direct cloud SQL connection — inside the backend itself.

Gateway-owned connections already have an execution path: a Gateway process
polls resolve_due_tests_for_gateway() and reports results back. A direct
connection has no equivalent external process, so without this module a
test mapped entirely to direct connections would sit in monitoring_schedules
forever with last_run=never — which is exactly what surfaced as "the
controls are not running." This mirrors resolve_due_tests_for_gateway's
due-test resolution logic (see execution_service.py) but scoped to
connection_mode == 'direct' connections, then evaluates the rule itself
(via rule_evaluation, the pandas-free port of gateway/gateway/rule_engine.py)
and records the result through the exact same record_execution_report used
by the Gateway path, so a run looks identical in the UI either way.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_test import AuditTest, TestDataMapping, TestRule
from app.models.data_source import DataConnection, DataEntity, DataField
from app.models.monitoring import MonitoringSchedule
from app.schemas.audit_engine import ExceptionReport, ExecutionReport
from app.schemas.test_rule import required_objects_for
from app.services import rule_evaluation
from app.services.data_source_service import fetch_direct_records
from app.services.execution_service import record_execution_report
from app.services.rule_parameter_service import get_parameters, resolve_parameters

logger = logging.getLogger("app.direct_execution")


def _describe_error(exc: Exception) -> str:
    """A raw pymongo/SQLAlchemy exception is a wall of shard hostnames and
    driver internals — exactly the kind of thing an auditor reading the
    Executions log should never have to parse. This translates the common,
    recognizable failure shapes into one plain sentence; anything
    unrecognized still gets a short, readable summary rather than the full
    traceback text. The raw exception is still captured by logger.exception
    above for whoever needs to actually debug it."""
    try:
        from pymongo.errors import ConfigurationError, OperationFailure, PyMongoError, ServerSelectionTimeoutError
    except ImportError:  # pragma: no cover — pymongo always installed here, but never let the import itself break error reporting
        ConfigurationError = OperationFailure = PyMongoError = ServerSelectionTimeoutError = ()  # type: ignore[assignment]

    text = str(exc)
    if isinstance(exc, ServerSelectionTimeoutError):
        if "SSL" in text or "TLS" in text:
            return (
                "Could not reach the database — the connection's TLS/SSL handshake failed. This usually means "
                "the network this platform is running on is blocking or intercepting encrypted traffic to the "
                "database's port, not a problem with the mapping or the rule itself."
            )
        return "Could not reach the database within the timeout — the host may be unreachable from this network."
    if isinstance(exc, OperationFailure):
        return "The database rejected the connection's credentials — check the username and password on this connection."
    if isinstance(exc, ConfigurationError):
        return "This connection is misconfigured — check the host, port, and database name."
    if isinstance(exc, PyMongoError):
        return "Could not connect to the database — check the connection's host, credentials, and network access."

    try:
        from sqlalchemy.exc import DBAPIError, OperationalError
    except ImportError:  # pragma: no cover
        DBAPIError = OperationalError = ()  # type: ignore[assignment]

    if isinstance(exc, (OperationalError, DBAPIError)):
        return "Could not connect to the database — check the connection's host, credentials, and network access."

    if isinstance(exc, KeyError):
        return f"The rule refers to a field or table ({exc}) that isn't mapped — re-check this control's field mapping."

    # Truncated rather than dropped — an unrecognized error is still worth
    # a short pointer, just not the full multi-hundred-line dump.
    return f"Execution failed: {text[:200]}"

# Bounded read per run — enough for a demo/mid-size collection or table
# while keeping one execution cycle fast; a genuinely huge object would need
# a real streaming/aggregation approach, not a flat fetch-everything.
_FETCH_LIMIT = 5000

# Mirrors execution_service._READY_STATUSES — only an explicitly
# reviewed-and-approved mapping is execution-ready, here as in the Gateway
# path.
_READY_STATUSES = {"approved"}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            return float(value)
    except ImportError:
        pass
    # Anything else (bson ObjectId/Decimal128, UUID, bytes, ...) — stringify
    # rather than fail the whole execution over a display value.
    return str(value)


class _DueDirectTest:
    def __init__(self, audit_test_id: uuid.UUID, schedule_id: uuid.UUID, rule_id: uuid.UUID, rule_definition: dict, objects: dict[str, dict]):
        self.audit_test_id = audit_test_id
        self.schedule_id = schedule_id
        self.rule_id = rule_id
        self.rule_definition = rule_definition
        self.objects = objects  # canonical_object -> {"connection": DataConnection, "entity_name": str, "fields": {canonical: physical}}


def _resolve_due_direct_tests(db: Session) -> list[_DueDirectTest]:
    now = datetime.now(timezone.utc)
    connections = db.scalars(
        select(DataConnection).where(
            DataConnection.connection_mode == "direct", DataConnection.connection_status == "connected"
        )
    ).all()
    if not connections:
        return []
    # A data source can have more than one direct connection on record (e.g.
    # a stale/pending one alongside the live one) — the first connected one
    # found is used, same tie-break sample_distinct_values already applies.
    connection_by_source: dict[uuid.UUID, DataConnection] = {}
    for c in connections:
        connection_by_source.setdefault(c.data_source_id, c)

    due: list[_DueDirectTest] = []
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
        objects: dict[str, dict] = {}
        for mapping in mappings:
            if not mapping.canonical_field or "." not in mapping.canonical_field:
                continue
            canonical_object, canonical_field = mapping.canonical_field.split(".", 1)
            connection = connection_by_source.get(mapping.data_source_id)
            if connection is None:
                continue  # this mapping's data source has no live direct connection
            entity = db.get(DataEntity, mapping.entity_id)
            field = db.get(DataField, mapping.field_id) if mapping.field_id else None
            if entity is None or field is None:
                continue

            if canonical_object not in objects:
                objects[canonical_object] = {"connection": connection, "entity_name": entity.entity_name, "fields": {}}
            elif objects[canonical_object]["connection"].connection_id != connection.connection_id or objects[canonical_object]["entity_name"] != entity.entity_name:
                continue  # conflicting mappings for the same object — skip rather than guess
            objects[canonical_object]["fields"][canonical_field] = field.field_name

        if not needed_objects.issubset(objects.keys()):
            continue  # not fully mapped to a reachable direct connection yet

        due.append(
            _DueDirectTest(
                audit_test_id=schedule.audit_test_id,
                schedule_id=schedule.schedule_id,
                rule_id=rule.rule_id,
                rule_definition=rule_definition,
                objects=objects,
            )
        )
    return due


def _run_one(due: _DueDirectTest) -> ExecutionReport:
    started_at = datetime.now(timezone.utc)
    try:
        records_by_object: dict[str, list[dict]] = {}
        for canonical_object, obj in due.objects.items():
            connection: DataConnection = obj["connection"]
            physical_fields = list(obj["fields"].values())
            raw_rows = fetch_direct_records(connection, entity_name=obj["entity_name"], field_names=physical_fields, limit=_FETCH_LIMIT)
            rename = {physical: canonical for canonical, physical in obj["fields"].items()}
            records_by_object[canonical_object] = [
                {rename.get(k, k): _json_safe(v) for k, v in row.items() if k in rename} for row in raw_rows
            ]

        result = rule_evaluation.evaluate(due.rule_definition, records_by_object)
        completed_at = datetime.now(timezone.utc)
        return ExecutionReport(
            audit_test_id=due.audit_test_id,
            schedule_id=due.schedule_id,
            rule_id=due.rule_id,
            started_at=started_at,
            completed_at=completed_at,
            status="completed",
            records_analyzed=result.records_analyzed,
            exceptions=[ExceptionReport(**e) for e in result.exceptions],
        )
    except Exception as exc:  # noqa: BLE001 — a failed test must be reported as FAILED, never silently dropped
        logger.exception("Direct execution failed for audit test %s", due.audit_test_id)
        return ExecutionReport(
            audit_test_id=due.audit_test_id,
            schedule_id=due.schedule_id,
            rule_id=due.rule_id,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            status="failed",
            error_message=_describe_error(exc),
        )


def run_due_direct_tests(db: Session) -> int:
    """Called on a timer from app.main's background loop. Returns how many
    tests were executed this cycle (0 is normal — most cycles have nothing
    due)."""
    due_tests = _resolve_due_direct_tests(db)
    for due in due_tests:
        report = _run_one(due)
        record_execution_report(db, report=report)
    return len(due_tests)
