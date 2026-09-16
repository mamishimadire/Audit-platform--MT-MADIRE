import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.evidence_exception import Exception_
from app.models.finding import Finding, FindingRootCause, RemediationAction, Retest
from app.models.monitoring import TestExecution
from app.schemas.finding import FindingCreate, RemediationActionCreate, RootCauseCreate
from app.services.audit_log_service import log_action
from app.services.exception_service import get_control_for_audit_test, sod_required


def create_finding(
    db: Session, *, exception_id: uuid.UUID, payload: FindingCreate, organization_id: uuid.UUID, created_by_user_id: uuid.UUID
) -> Finding:
    finding = Finding(
        organization_id=organization_id,
        exception_id=exception_id,
        finding_title=payload.finding_title,
        finding_description=payload.finding_description,
        risk_rating=payload.risk_rating,
        status="open",
        created_by=created_by_user_id,
    )
    db.add(finding)
    db.flush()
    log_action(
        db,
        action=f"Created finding '{finding.finding_title}'",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="findings",
        entity_id=finding.finding_id,
        new_value={"finding_title": finding.finding_title, "risk_rating": finding.risk_rating},
    )
    db.commit()
    db.refresh(finding)
    return finding


def list_findings(db: Session, *, organization_id: uuid.UUID) -> list[Finding]:
    findings = list(db.scalars(select(Finding).where(Finding.organization_id == organization_id)))
    for finding in findings:
        # Lets the Findings page group by control instead of one flat,
        # uncategorized list — same chain (exception -> execution ->
        # audit_test -> control) list_exceptions_for_organization already
        # walks for its own control_code/control_name attachment.
        control_code = control_name = None
        exception = db.get(Exception_, finding.exception_id)
        execution = db.get(TestExecution, exception.execution_id) if exception else None
        if execution is not None:
            control = get_control_for_audit_test(db, audit_test_id=execution.audit_test_id)
            if control is not None:
                control_code, control_name = control.control_code, control.control_name
        finding.control_code = control_code
        finding.control_name = control_name
    return findings


def create_root_cause(db: Session, *, finding_id: uuid.UUID, payload: RootCauseCreate) -> FindingRootCause:
    root_cause = FindingRootCause(finding_id=finding_id, root_cause_category=payload.root_cause_category, description=payload.description)
    db.add(root_cause)
    db.commit()
    db.refresh(root_cause)
    return root_cause


def list_root_causes(db: Session, *, finding_id: uuid.UUID) -> list[FindingRootCause]:
    return list(db.scalars(select(FindingRootCause).where(FindingRootCause.finding_id == finding_id)))


def create_remediation_action(
    db: Session, *, finding_id: uuid.UUID, payload: RemediationActionCreate, organization_id: uuid.UUID, created_by_user_id: uuid.UUID
) -> RemediationAction:
    action = RemediationAction(
        finding_id=finding_id,
        action_description=payload.action_description,
        responsible_user_id=payload.responsible_user_id,
        target_date=payload.target_date,
        status="pending",
        created_by=created_by_user_id,
    )
    db.add(action)
    db.flush()

    finding = db.get(Finding, finding_id)
    if finding is not None and finding.status in ("open", "reopened"):
        # A finding can enter remediation from a clean 'open' state or after a
        # failed re-test sent it back to 'reopened' — both are valid starts
        # of a new remediation cycle.
        finding.status = "remediation_in_progress"

    log_action(
        db,
        action="Created remediation action",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="remediation_actions",
        entity_id=action.remediation_id,
        new_value={"finding_id": str(finding_id), "target_date": str(payload.target_date) if payload.target_date else None},
    )
    db.commit()
    db.refresh(action)
    return action


def list_remediation_actions(db: Session, *, finding_id: uuid.UUID) -> list[RemediationAction]:
    return list(db.scalars(select(RemediationAction).where(RemediationAction.finding_id == finding_id)))


def update_remediation_status(db: Session, *, action: RemediationAction, status: str, organization_id: uuid.UUID, updated_by_user_id: uuid.UUID) -> RemediationAction:
    action.status = status
    if status == "completed":
        action.completed_at = datetime.now(timezone.utc)
        action.completed_by = updated_by_user_id
        finding = db.get(Finding, action.finding_id)
        if finding is not None and finding.status == "remediation_in_progress":
            # Management says it's fixed — the finding now awaits an auditor's
            # re-test. It is NOT closed here: closing only ever happens via an
            # explicit retest result (Section 22 — never auto-close on say-so).
            finding.status = "awaiting_retest"
    log_action(
        db,
        action=f"Remediation action status changed to '{status}'",
        organization_id=organization_id,
        user_id=updated_by_user_id,
        entity_type="remediation_actions",
        entity_id=action.remediation_id,
        new_value={"status": status},
    )
    db.commit()
    db.refresh(action)
    return action


def is_overdue(target_date: date | None, status: str) -> bool:
    if target_date is None or status == "completed":
        return False
    return target_date < datetime.now(timezone.utc).date()


def create_retest(
    db: Session, *, finding_id: uuid.UUID, audit_test_id: uuid.UUID, result: str, comments: str | None,
    organization_id: uuid.UUID, performed_by_user_id: uuid.UUID,
) -> Retest:
    """A retest is the independent evidence that remediation actually
    worked — when this org requires it, whoever marked the fix 'completed'
    cannot also be the one certifying that it worked."""
    if sod_required(db, organization_id=organization_id):
        completed_by_ids = {
            a.completed_by
            for a in db.scalars(select(RemediationAction).where(RemediationAction.finding_id == finding_id))
            if a.completed_by is not None
        }
        if performed_by_user_id in completed_by_ids:
            raise ValueError(
                "Segregation of duties: you performed this remediation — a different authorized user must verify it."
            )

    retest = Retest(
        finding_id=finding_id,
        audit_test_id=audit_test_id,
        result=result,
        comments=comments,
        performed_by=performed_by_user_id,
    )
    db.add(retest)
    db.flush()

    finding = db.get(Finding, finding_id)
    if finding is not None:
        finding.status = "closed" if result == "pass" else "reopened"

    log_action(
        db,
        action=f"Re-test performed: {result.upper()}",
        organization_id=organization_id,
        user_id=performed_by_user_id,
        entity_type="retests",
        entity_id=retest.retest_id,
        new_value={"result": result, "finding_status": finding.status if finding else None},
    )
    db.commit()
    db.refresh(retest)
    return retest


def list_retests(db: Session, *, finding_id: uuid.UUID) -> list[Retest]:
    return list(db.scalars(select(Retest).where(Retest.finding_id == finding_id)))


def list_remediation_actions_for_organization(db: Session, *, organization_id: uuid.UUID) -> list[RemediationAction]:
    return list(
        db.scalars(
            select(RemediationAction)
            .join(Finding, Finding.finding_id == RemediationAction.finding_id)
            .where(Finding.organization_id == organization_id)
        )
    )


def list_retests_for_organization(db: Session, *, organization_id: uuid.UUID) -> list[Retest]:
    return list(
        db.scalars(
            select(Retest).join(Finding, Finding.finding_id == Retest.finding_id).where(Finding.organization_id == organization_id)
        )
    )
