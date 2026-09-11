import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_test import AuditTest, ControlAuditTest
from app.models.business_process import BusinessProcess
from app.models.evidence_exception import Exception_
from app.models.finding import Finding
from app.models.monitoring import TestExecution
from app.models.risk_control import Control, Risk, RiskControl
from app.schemas.finding import TraceNode


def trace_from_finding(db: Session, *, finding: Finding) -> list[TraceNode]:
    """
    Walks a finding backward to its originating business process — the
    traceability the product spec calls out explicitly (Section 2): a user
    must be able to start from a finding and see the exception, execution,
    audit test, control, risk and business process that produced it.
    """
    chain: list[TraceNode] = [TraceNode(level="finding", id=str(finding.finding_id), label=finding.finding_title)]

    exception = db.get(Exception_, finding.exception_id)
    if exception is None:
        return chain
    chain.append(TraceNode(level="exception", id=str(exception.exception_id), label=exception.exception_description or "Exception"))

    execution = db.get(TestExecution, exception.execution_id)
    if execution is None:
        return chain
    chain.append(
        TraceNode(level="execution", id=str(execution.execution_id), label=f"Run at {execution.started_at.isoformat()} — {execution.status}")
    )

    audit_test = db.get(AuditTest, execution.audit_test_id)
    if audit_test is None:
        return chain
    chain.append(TraceNode(level="audit_test", id=str(audit_test.audit_test_id), label=audit_test.test_name))

    control_ids = list(db.scalars(select(ControlAuditTest.control_id).where(ControlAuditTest.audit_test_id == audit_test.audit_test_id)))
    controls = list(db.scalars(select(Control).where(Control.control_id.in_(control_ids)))) if control_ids else []
    for control in controls:
        chain.append(TraceNode(level="control", id=str(control.control_id), label=control.control_name))

    risk_ids = (
        list(db.scalars(select(RiskControl.risk_id).where(RiskControl.control_id.in_(control_ids)))) if control_ids else []
    )
    risks = list(db.scalars(select(Risk).where(Risk.risk_id.in_(risk_ids)))) if risk_ids else []
    for risk in risks:
        chain.append(TraceNode(level="risk", id=str(risk.risk_id), label=risk.risk_name))

    process_ids = {r.process_id for r in risks if r.process_id is not None}
    processes = list(db.scalars(select(BusinessProcess).where(BusinessProcess.process_id.in_(process_ids)))) if process_ids else []
    for process in processes:
        chain.append(TraceNode(level="business_process", id=str(process.process_id), label=process.process_name))

    return chain
