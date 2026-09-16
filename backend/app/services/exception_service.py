import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_test import AuditTest, ControlAuditTest, TestRule
from app.models.control_library import ControlLibraryEntry
from app.models.evidence_exception import Exception_, ExceptionRecord
from app.models.monitoring import TestExecution
from app.models.organization import OrganizationSetting
from app.models.risk_control import Control, Risk, RiskControl
from app.services.audit_log_service import log_action

_CLOSING_STATUSES = ("resolved", "closed")
SOD_EXCEPTION_CLOSURE_SETTING = "sod_required_for_exception_closure"


def sod_required(db: Session, *, organization_id: uuid.UUID) -> bool:
    """
    Opt-in per organization via the existing organization_settings table
    (no new table needed) — defaults to off. A hard-coded global rule would
    immediately block a single person testing/demoing the whole platform
    solo (creating, owning, and closing their own test exception is exactly
    that workflow); clients who want the real control get to turn it on.

    Reused beyond exception closure itself — finding_service.create_retest
    checks the same flag to enforce remediation performer != verifier,
    since both are "does this org want a second person on closure-type
    actions" and a second, separate setting would just be two knobs
    controlling the same underlying policy question.
    """
    setting = db.scalar(
        select(OrganizationSetting).where(
            OrganizationSetting.organization_id == organization_id,
            OrganizationSetting.setting_name == SOD_EXCEPTION_CLOSURE_SETTING,
        )
    )
    return setting is not None and setting.setting_value == "true"

# An exception in any of these statuses is still "the same ongoing issue" —
# a fresh detection of it updates that row instead of inserting a new one.
# 'resolved'/'closed' are deliberately excluded: if the issue recurs after
# being marked fixed, that recurrence is a genuinely new exception, not a
# continuation — auto-merging it back in would let a real regression hide
# behind an old, already-closed record.
OPEN_STATUSES = ("open", "awaiting_evidence", "in_progress")


def find_open_exception(db: Session, *, audit_test_id: uuid.UUID, description: str) -> Exception_ | None:
    return db.scalar(
        select(Exception_)
        .join(TestExecution, TestExecution.execution_id == Exception_.execution_id)
        .where(
            TestExecution.audit_test_id == audit_test_id,
            Exception_.exception_description == description,
            Exception_.status.in_(OPEN_STATUSES),
        )
        .order_by(Exception_.last_detected_at.desc())
        .limit(1)
    )


# A plain-English fallback per rule shape, used whenever an exception has
# no more specific recommended_remediation of its own — that field exists
# on the model but nothing populates it yet (see Exception_.recommended_
# remediation), so this is the real source of "what to do" today.
_RULE_TYPE_RECOMMENDATION = {
    "threshold": "Look at this record and check it against your policy. Then fix it, get it approved, or mark it as OK.",
    "duplicate": "Look at these matching records together. If one is a mistake, merge them or remove the extra one.",
    "missing_match": "Find out why the link is missing. If it should exist, add it. If it shouldn't, find out why this record is here at all.",
    "cross_match_condition": "Look at both records together and fix whichever one is wrong — usually by turning off access, changing a status, or getting the missing approval.",
    "three_way_match": "Compare all three records side by side (for example, the order, the delivery, and the invoice) and fix whichever one doesn't match the other two.",
}


def _humanize_field_name(key: str) -> str:
    """Turns a raw field name like 'employment_status_primary' into
    'Employment Status' — strips the _primary/_secondary/_tertiary role
    suffix the rule engine adds, then title-cases each word."""
    for suffix in ("_primary", "_secondary", "_tertiary"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
            break
    words = key.replace("_", " ").split()
    return " ".join(word.upper() if word.lower() == "id" else word.capitalize() for word in words)


def explain_exception(db: Session, *, exception: Exception_) -> dict:
    """Everything a non-technical reader needs to understand one exception:
    what was actually found (the real field values from the record that
    failed), why it matters (the real, linked risk this control exists to
    catch — not a generic warning), and what to do about it. Built on the
    fly from data that already exists (exception_records, the control's
    own audit_procedure, its linked risk's risk_description) rather than
    hand-written per control, so it stays accurate as mappings/data change
    and covers all 157 controls without anyone writing 157 explanations."""
    execution = db.get(TestExecution, exception.execution_id)
    audit_test = db.get(AuditTest, execution.audit_test_id) if execution else None
    rule = db.get(TestRule, execution.rule_id) if execution and execution.rule_id else None

    control: Control | None = None
    library_entry: ControlLibraryEntry | None = None
    risk: Risk | None = None
    if audit_test is not None:
        link = db.scalar(select(ControlAuditTest).where(ControlAuditTest.audit_test_id == audit_test.audit_test_id))
        if link is not None:
            control = db.get(Control, link.control_id)
            if control is not None:
                if control.control_library_id is not None:
                    library_entry = db.get(ControlLibraryEntry, control.control_library_id)
                risk_link = db.scalar(select(RiskControl).where(RiskControl.control_id == control.control_id))
                if risk_link is not None:
                    risk = db.get(Risk, risk_link.risk_id)

    records = list(db.scalars(select(ExceptionRecord).where(ExceptionRecord.exception_id == exception.exception_id)))
    facts = [
        {"label": _humanize_field_name(key), "value": "(no value)" if value is None else str(value)}
        for key, value in (records[0].exception_data or {}).items()
    ] if records else []

    control_label = f"{control.control_code} — {control.control_name}" if control else (audit_test.test_name if audit_test else "This control")
    # exception.exception_description is an internal grouping key ("{test
    # name}: exception on {record id}", set by execution_service so repeat
    # detections of the SAME record update one row instead of piling up
    # duplicates) — not written to be read aloud. Restating it here used to
    # make the summary say the control's name twice in one sentence; the
    # record's own identifier plus the control's name says the same thing
    # once, plainly.
    record_id = records[0].record_identifier if records else None
    summary = (
        f"Record {record_id} did not pass the \"{control_label}\" check."
        if record_id
        else f"A record did not pass the \"{control_label}\" check."
    )

    why_it_matters = (
        risk.risk_description
        if risk is not None and risk.risk_description
        else (
            f"This control exists to check: {library_entry.audit_procedure}"
            if library_entry is not None
            else "This check exists to catch a real problem — when it fails, it usually means something needs a closer look."
        )
    )

    what_to_do = exception.recommended_remediation or _RULE_TYPE_RECOMMENDATION.get(
        rule.rule_type if rule else None, "Look into this record and decide what needs to change."
    )

    return {
        "summary": summary,
        "facts": facts,
        "why_it_matters": why_it_matters,
        "what_to_do": what_to_do,
        "seen_count": exception.occurrence_count,
        "first_seen": exception.detected_at,
        "last_seen": exception.last_detected_at,
    }


def update_exception(
    db: Session, *, exception: Exception_, status: str | None, owner_id: uuid.UUID | None, organization_id: uuid.UUID,
    updated_by_user_id: uuid.UUID,
) -> Exception_:
    old_status = exception.status
    if (
        status in _CLOSING_STATUSES
        and exception.owner_id is not None
        and exception.owner_id == updated_by_user_id
        and sod_required(db, organization_id=organization_id)
    ):
        raise ValueError(
            "Segregation of duties: the assigned owner of this exception cannot also close it — "
            "have another authorized user review and close it instead."
        )
    if status is not None:
        exception.status = status
    if owner_id is not None:
        exception.owner_id = owner_id
    log_action(
        db,
        action=f"Exception status changed: {old_status} -> {exception.status}",
        organization_id=organization_id,
        user_id=updated_by_user_id,
        entity_type="exceptions",
        entity_id=exception.exception_id,
        old_value={"status": old_status},
        new_value={"status": exception.status, "owner_id": str(exception.owner_id) if exception.owner_id else None},
    )
    db.commit()
    db.refresh(exception)
    return exception
