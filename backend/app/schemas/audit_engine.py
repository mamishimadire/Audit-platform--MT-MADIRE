import uuid
from datetime import date, datetime
from typing import Any

from app.schemas.common import OrmModel
from app.schemas.test_rule import TestRuleDefinition


class TestRuleCreate(OrmModel):
    rule_name: str
    severity: str | None = None  # low | medium | high | critical
    rule_definition: TestRuleDefinition


class TestRuleOut(OrmModel):
    rule_id: uuid.UUID
    audit_test_id: uuid.UUID
    rule_name: str
    rule_type: str | None
    rule_definition: dict
    severity: str | None
    status: str
    origin: str
    template_id: uuid.UUID | None
    created_by: uuid.UUID | None
    edited_by: uuid.UUID | None
    edited_at: datetime | None
    needs_review: bool
    deleted_reason: str | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    rejected_reason: str | None
    version: int
    supersedes_rule_id: uuid.UUID | None


class RuleDeleteRequest(OrmModel):
    reason: str


class RuleRejectRequest(OrmModel):
    reason: str


class MonitoringScheduleCreate(OrmModel):
    frequency: str  # real_time | hourly | daily | weekly | monthly
    is_active: bool = True


class MonitoringScheduleOut(OrmModel):
    schedule_id: uuid.UUID
    audit_test_id: uuid.UUID
    frequency: str
    next_run: datetime | None
    last_run: datetime | None
    is_active: bool


class TestExecutionOut(OrmModel):
    execution_id: uuid.UUID
    audit_test_id: uuid.UUID
    started_at: datetime
    completed_at: datetime | None
    status: str  # running | completed | failed
    records_analyzed: int | None
    exceptions_found: int | None
    execution_log: str | None


class EvidenceOut(OrmModel):
    evidence_id: uuid.UUID
    execution_id: uuid.UUID
    evidence_type: str | None
    evidence_location: str | None
    evidence_hash: str | None
    created_at: datetime


class ExceptionRecordOut(OrmModel):
    exception_record_id: uuid.UUID
    exception_id: uuid.UUID
    record_identifier: str | None
    exception_data: dict | None


class ExceptionOut(OrmModel):
    exception_id: uuid.UUID
    execution_id: uuid.UUID
    exception_reference: str | None
    exception_description: str | None
    recommended_remediation: str | None
    severity: str | None
    status: str
    owner_id: uuid.UUID | None
    detected_at: datetime
    last_detected_at: datetime
    occurrence_count: int
    has_finding: bool = False


class ExceptionUpdate(OrmModel):
    status: str | None = None  # open | awaiting_evidence | in_progress | resolved | closed
    owner_id: uuid.UUID | None = None


# --- Gateway-facing: pulling due work and reporting results ---


class DueTestObject(OrmModel):
    connection_id: uuid.UUID
    entity_name: str
    fields: dict[str, str]  # canonical field name -> physical field name


class DueTest(OrmModel):
    audit_test_id: uuid.UUID
    schedule_id: uuid.UUID
    rule_id: uuid.UUID
    rule_definition: dict[str, Any]
    objects: dict[str, DueTestObject]  # canonical object name -> where to find it


class ExceptionReport(OrmModel):
    record_identifier: str
    exception_data: dict[str, Any]


class ExecutionReport(OrmModel):
    audit_test_id: uuid.UUID
    schedule_id: uuid.UUID
    # The exact rule version the Gateway was told to run (echoed back from
    # DueTest.rule_id) — recorded on the execution so historical runs can
    # always be traced to the specific, immutable rule version that
    # produced them, not just "whatever the active rule is today." Optional
    # for backward compatibility with a Gateway build that predates this
    # field; new Gateways should always send it.
    rule_id: uuid.UUID | None = None
    started_at: datetime
    completed_at: datetime
    status: str  # completed | failed
    records_analyzed: int | None = None
    exceptions: list[ExceptionReport] = []
    error_message: str | None = None
