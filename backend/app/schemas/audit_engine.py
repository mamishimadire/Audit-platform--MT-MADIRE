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
    # pending_approval | active | rejected | superseded — see
    # MonitoringSchedule's own docstring. A schedule is never picked up by
    # the Gateway (resolve_due_tests_for_gateway) until this is 'active'.
    status: str
    created_by: uuid.UUID | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    rejected_reason: str | None
    version: int
    supersedes_schedule_id: uuid.UUID | None


class ScheduleRejectRequest(OrmModel):
    reason: str


class TestExecutionOut(OrmModel):
    execution_id: uuid.UUID
    audit_test_id: uuid.UUID
    started_at: datetime
    completed_at: datetime | None
    status: str  # see app.core.execution_status — pass | exception | mapping_required | not_testable | insufficient_data | error
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
    # Only set by list_evidence_for_organization — a plain-English "what
    # is this" sentence, plus the control/test it came from, so the
    # Evidence page never has to show the raw evidence_location JSON as
    # the primary read.
    summary: str | None = None
    test_name: str | None = None
    control_code: str | None = None
    control_name: str | None = None


class ExceptionRecordOut(OrmModel):
    exception_record_id: uuid.UUID
    exception_id: uuid.UUID
    record_identifier: str | None
    exception_data: dict | None
    detected_at: datetime


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
    # Only set by list_exceptions_for_organization (see execution_service.py)
    # — the plain-English explain_exception() breakdown, attached in bulk so
    # a list of exceptions (Executions/Exceptions pages) never needs a
    # separate per-row fetch just to show what a reader actually needs to
    # know. None on every other path that returns a bare Exception_ (get/
    # update) — those still have the dedicated GET .../explanation endpoint
    # for the full breakdown (facts table included).
    summary: str | None = None
    why_it_matters: str | None = None
    what_to_do: str | None = None
    # Also only set by list_exceptions_for_organization — lets the
    # Exceptions/Findings pages group by control instead of showing one
    # flat, uncategorized list. None for a manually-created audit test with
    # no control_library link (nothing to group it under).
    control_code: str | None = None
    control_name: str | None = None
    # Also only set by list_exceptions_for_organization — lets a UI match
    # "every currently-open exception for THIS test" by audit_test_id
    # rather than requiring an exact execution_id match, which moves
    # forward on every re-detection and can be a request or two ahead of
    # a separately-fetched executions list on a frequently re-running test.
    audit_test_id: uuid.UUID | None = None


class ExceptionUpdate(OrmModel):
    status: str | None = None  # open | awaiting_evidence | in_progress | resolved | closed
    owner_id: uuid.UUID | None = None


class ExceptionFactOut(OrmModel):
    label: str
    value: str


class ExceptionExplanationOut(OrmModel):
    """A plain-English breakdown of one exception — what was found, the
    real field values behind it, why it matters, and what to do — so
    reading it doesn't require already knowing how the rule engine works."""

    summary: str
    facts: list[ExceptionFactOut]
    why_it_matters: str
    what_to_do: str
    seen_count: int
    first_seen: datetime
    last_seen: datetime


class TraceFieldOut(OrmModel):
    field: str
    value: str


class TraceObjectOut(OrmModel):
    role: str | None  # primary | secondary | tertiary | quaternary | None (single-object rules)
    canonical_object: str
    table_name: str | None  # the physical table this canonical object is mapped to, if any
    fields: list[TraceFieldOut]


class ExceptionTraceOut(OrmModel):
    """Control -> test run -> physical table/field values -> result — the
    'why did the system reach this conclusion' lineage view, distinct from
    ExceptionExplanationOut (a plain-English summary) and from
    FindingTrace (which walks a Finding UP to its business process)."""

    exception_id: uuid.UUID
    control_code: str | None
    control_name: str | None
    execution_id: uuid.UUID
    executed_at: datetime
    rule_type: str | None
    summary: str
    objects: list[TraceObjectOut]
    # Any exception_data key that couldn't be confidently attributed to
    # one specific object/table — shown, never hidden, but not claimed to
    # be from a table it might not actually be from.
    other_fields: list[TraceFieldOut]
    severity: str | None
    status: str


class EvidenceRequestCreate(OrmModel):
    description: str
    due_date: date | None = None


class EvidenceFileOut(OrmModel):
    evidence_file_id: uuid.UUID
    request_id: uuid.UUID
    file_name: str
    content_type: str | None
    uploaded_by: uuid.UUID | None
    uploaded_at: datetime
    # Same reasoning as EvidenceRequestOut.requested_by_name below.
    uploaded_by_name: str | None = None
    uploaded_by_role: str | None = None


class EvidenceRequestOut(OrmModel):
    request_id: uuid.UUID
    exception_id: uuid.UUID
    description: str
    due_date: date | None
    status: str  # awaiting | received
    requested_by: uuid.UUID | None
    requested_at: datetime
    files: list[EvidenceFileOut] = []
    # Resolved server-side (see routes/exceptions.py._resolve_user_display)
    # rather than making the frontend match requested_by/uploaded_by
    # against a users list — an internal auditor viewing/acting on a
    # client's exception is never IN that client's own /organizations/{id}/
    # users list, so a client-scoped lookup would show "Unknown user" for
    # anything the audit team itself did.
    requested_by_name: str | None = None
    requested_by_role: str | None = None


class ExceptionCommentCreate(OrmModel):
    body: str


class ExceptionCommentOut(OrmModel):
    comment_id: uuid.UUID
    exception_id: uuid.UUID
    author_id: uuid.UUID | None
    body: str
    created_at: datetime
    # Same reasoning as EvidenceRequestOut.requested_by_name — resolved
    # server-side so an internal auditor's own comment never shows as
    # "Unknown user" to a client viewing their own organization's thread.
    author_name: str | None = None
    author_role: str | None = None


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
    status: str  # see app.core.execution_status — pass | exception | mapping_required | insufficient_data | error (a Gateway never reports not_testable — see that module's docstring)
    records_analyzed: int | None = None
    exceptions: list[ExceptionReport] = []
    error_message: str | None = None
