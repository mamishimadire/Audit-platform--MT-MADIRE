import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_test import AuditTest, ControlAuditTest, TestRule
from app.models.control_library import ControlRuleTemplate
from app.models.risk_control import Control
from app.schemas.audit_engine import TestRuleCreate
from app.services.audit_log_service import log_action
from app.services.control_binding_service import is_fully_bound


def create_test_rule(
    db: Session, *, audit_test_id: uuid.UUID, payload: TestRuleCreate, organization_id: uuid.UUID, created_by_user_id: uuid.UUID
) -> TestRule:
    """Every rule starts life needing a second person's sign-off — see
    approve_rule. Nothing runs against real data until that happens,
    regardless of how the rule was written."""
    rule = TestRule(
        audit_test_id=audit_test_id,
        rule_name=payload.rule_name,
        rule_type=payload.rule_definition.rule_type,
        rule_definition=payload.rule_definition.model_dump_json(),
        severity=payload.severity,
        status="pending_approval",
        origin="manual",
        created_by=created_by_user_id,
    )
    db.add(rule)
    db.flush()
    log_action(
        db,
        action=f"Created test rule '{rule.rule_name}' (pending approval)",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="test_rules",
        entity_id=rule.rule_id,
        new_value={"rule_type": rule.rule_type, "status": "pending_approval"},
    )
    db.commit()
    db.refresh(rule)
    return rule


def approve_rule(db: Session, *, rule: TestRule, approved_by_user_id: uuid.UUID, organization_id: uuid.UUID) -> TestRule:
    """Maker-checker, identity-based — same pattern as mapping_service.approve_mapping.
    Whoever created or last edited this rule cannot also be the one who approves it,
    even though both people may hold the exact same permission."""
    maker_id = rule.edited_by or rule.created_by
    if maker_id is not None and maker_id == approved_by_user_id:
        raise ValueError("You wrote this rule yourself — a different authorized user must approve it.")
    if rule.status != "pending_approval":
        raise ValueError(f"This rule is '{rule.status}', not pending approval.")

    rule.status = "active"
    rule.approved_by = approved_by_user_id
    rule.approved_at = datetime.now(timezone.utc)
    rule.rejected_reason = None
    log_action(
        db,
        action=f"Approved test rule '{rule.rule_name}'",
        organization_id=organization_id,
        user_id=approved_by_user_id,
        entity_type="test_rules",
        entity_id=rule.rule_id,
        new_value={"status": "active"},
    )
    _maybe_auto_activate_control(db, audit_test_id=rule.audit_test_id, organization_id=organization_id, approved_by_user_id=approved_by_user_id)
    db.commit()
    db.refresh(rule)
    return rule


def _maybe_auto_activate_control(
    db: Session, *, audit_test_id: uuid.UUID, organization_id: uuid.UUID, approved_by_user_id: uuid.UUID
) -> None:
    """A rule only reaches 'active' after its own maker-checker approval,
    on top of the mapping approvals that got its fields there in the first
    place — by the time this runs, the control's setup has already been
    through two independent review cycles. Requiring a THIRD, separate
    "request activation" / "approve activation" cycle on top of those
    (control_service.request_activation/approve_activation) adds process
    without adding assurance, so a control still sitting in
    'pending_mapping' is activated automatically the moment its rule goes
    live, provided its required tables are fully bound. A control someone
    has already put through the manual request_activation flow (now
    'pending_activation') is left untouched — that explicit approval still
    applies exactly as before, this only short-circuits the case where
    nobody has started it."""
    link = db.scalar(select(ControlAuditTest).where(ControlAuditTest.audit_test_id == audit_test_id))
    if link is None:
        return
    control = db.get(Control, link.control_id)
    if control is None or control.status != "pending_mapping":
        return
    if not is_fully_bound(db, control=control):
        return

    control.status = "active"
    control.activation_approved_by = approved_by_user_id
    log_action(
        db,
        action=f"Auto-activated control '{control.control_code} — {control.control_name}' — its test rule was just approved and all required tables are bound",
        organization_id=organization_id,
        user_id=approved_by_user_id,
        entity_type="controls",
        entity_id=control.control_id,
        new_value={"status": "active"},
    )
    linked_tests = db.scalars(
        select(AuditTest)
        .join(ControlAuditTest, ControlAuditTest.audit_test_id == AuditTest.audit_test_id)
        .where(ControlAuditTest.control_id == control.control_id)
    )
    for test in linked_tests:
        test.status = "active"


def reject_rule(db: Session, *, rule: TestRule, reason: str, rejected_by_user_id: uuid.UUID, organization_id: uuid.UUID) -> TestRule:
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reject a test rule.")
    if rule.status != "pending_approval":
        raise ValueError(f"This rule is '{rule.status}', not pending approval.")

    rule.status = "rejected"
    rule.rejected_reason = reason
    log_action(
        db,
        action=f"Rejected test rule '{rule.rule_name}': {reason}",
        organization_id=organization_id,
        user_id=rejected_by_user_id,
        entity_type="test_rules",
        entity_id=rule.rule_id,
        new_value={"status": "rejected", "reason": reason},
    )
    db.commit()
    db.refresh(rule)
    return rule


def list_test_rules(db: Session, *, audit_test_id: uuid.UUID) -> list[TestRule]:
    """Current rules only — a superseded version stays in the database
    (supersedes_rule_id chain) but isn't shown in the normal list, same
    convention as mapping_service.list_mappings."""
    return list(
        db.scalars(
            select(TestRule).where(TestRule.audit_test_id == audit_test_id, TestRule.status != "superseded")
        )
    )


def get_control_rule_template(db: Session, *, audit_test_id: uuid.UUID) -> tuple[Control | None, ControlRuleTemplate | None]:
    """A test's control (via control_audit_tests) may have a rule template
    tied to its control_library entry — None, None if either link is
    missing (a manually-created test, or a control with no template yet)."""
    link = db.scalar(select(ControlAuditTest).where(ControlAuditTest.audit_test_id == audit_test_id))
    if link is None:
        return None, None
    control = db.get(Control, link.control_id)
    if control is None or control.control_library_id is None:
        return control, None
    template = db.scalar(select(ControlRuleTemplate).where(ControlRuleTemplate.control_library_id == control.control_library_id))
    return control, template


def generate_rule_from_template(
    db: Session, *, audit_test_id: uuid.UUID, organization_id: uuid.UUID, created_by_user_id: uuid.UUID
) -> TestRule:
    from app.services.mapping_service import get_readiness_against_template

    control, template = get_control_rule_template(db, audit_test_id=audit_test_id)
    if template is None:
        raise ValueError("This control has no rule template yet — create a rule manually below.")
    if not is_fully_bound(db, control=control):
        raise ValueError("Bind this control's required tables first (see the Controls page).")

    rule_definition = json.loads(template.rule_definition)
    readiness = get_readiness_against_template(db, audit_test_id=audit_test_id, rule_definition=rule_definition)
    if not readiness.ready:
        missing = [f"{o.canonical_object}.{f.canonical_field}" for o in readiness.objects for f in o.required_fields if not f.mapped]
        raise ValueError(f"Field mapping isn't complete yet — still missing: {', '.join(missing)}.")

    existing = db.scalar(
        select(TestRule).where(TestRule.audit_test_id == audit_test_id, TestRule.status.in_(("pending_approval", "active")))
    )
    if existing is not None:
        raise ValueError(f"This test already has a rule ('{existing.status}') — delete it first if you want to regenerate from the template.")

    # Auto-generated still needs a human to approve it before it runs — the
    # template's logic may be sound in general but wrong for this client's
    # specific mapping, and "the platform wrote it" is not the same as "an
    # auditor reviewed it."
    rule = TestRule(
        audit_test_id=audit_test_id,
        rule_name=template.rule_name,
        rule_type=rule_definition.get("rule_type"),
        rule_definition=template.rule_definition,
        status="pending_approval",
        origin="auto_generated",
        template_id=template.template_id,
        created_by=created_by_user_id,
    )
    db.add(rule)
    db.flush()
    log_action(
        db,
        action=f"Auto-generated test rule '{rule.rule_name}' from control template (pending approval)",
        organization_id=organization_id,
        user_id=created_by_user_id,
        entity_type="test_rules",
        entity_id=rule.rule_id,
        new_value={"template_id": str(template.template_id), "status": "pending_approval"},
    )
    db.commit()
    db.refresh(rule)
    return rule


def update_test_rule(
    db: Session, *, rule: TestRule, payload: TestRuleCreate, organization_id: uuid.UUID, updated_by_user_id: uuid.UUID
) -> TestRule:
    """Editing an auto-generated rule keeps its template_id (provenance —
    a reviewer can still see what it started from) but reclassifies origin
    so it's visibly no longer the unmodified library default.

    Immutability: an ACTIVE rule is never edited in place — that would let
    someone quietly change what a live control tests while the audit trail
    still shows it as "approved," for content nobody actually reviewed.
    Editing one instead creates a new version (supersedes_rule_id pointing
    back at this row, this row's status flipped to 'superseded'), which
    goes through pending_approval -> approve_rule again like any other new
    rule. A rule that was never approved yet (pending_approval/rejected)
    has nothing live to protect, so it's still fine to edit in place.
    """
    if rule.status == "active":
        new_rule = TestRule(
            audit_test_id=rule.audit_test_id,
            rule_name=payload.rule_name,
            rule_type=payload.rule_definition.rule_type,
            rule_definition=payload.rule_definition.model_dump_json(),
            severity=payload.severity,
            status="pending_approval",
            origin="auto_generated_edited" if rule.origin in ("auto_generated", "auto_generated_edited") else "manual",
            template_id=rule.template_id,
            created_by=updated_by_user_id,
            version=rule.version + 1,
            supersedes_rule_id=rule.rule_id,
        )
        db.add(new_rule)
        db.flush()
        rule.status = "superseded"
        log_action(
            db,
            action=f"Edited active test rule '{rule.rule_name}' — created version {new_rule.version}, pending re-approval",
            organization_id=organization_id,
            user_id=updated_by_user_id,
            entity_type="test_rules",
            entity_id=new_rule.rule_id,
            old_value={"status": "active", "version": rule.version, "rule_id": str(rule.rule_id)},
            new_value={"status": "pending_approval", "version": new_rule.version},
        )
        db.commit()
        db.refresh(new_rule)
        return new_rule

    rule.rule_name = payload.rule_name
    rule.rule_type = payload.rule_definition.rule_type
    rule.rule_definition = payload.rule_definition.model_dump_json()
    rule.severity = payload.severity
    rule.needs_review = False
    if rule.origin == "auto_generated":
        rule.origin = "auto_generated_edited"
    rule.edited_by = updated_by_user_id
    rule.edited_at = datetime.now(timezone.utc)
    rule.status = "pending_approval"
    rule.approved_by = None
    rule.approved_at = None
    rule.rejected_reason = None
    log_action(
        db,
        action=f"Edited test rule '{rule.rule_name}'",
        organization_id=organization_id,
        user_id=updated_by_user_id,
        entity_type="test_rules",
        entity_id=rule.rule_id,
        new_value={"rule_type": rule.rule_type, "origin": rule.origin, "status": "pending_approval"},
    )
    db.commit()
    db.refresh(rule)
    return rule


def delete_test_rule(db: Session, *, rule: TestRule, reason: str, organization_id: uuid.UUID, deleted_by_user_id: uuid.UUID) -> TestRule:
    """Soft delete only — never removes the row. A reviewer must be able to
    see that a control's standard test logic was deliberately turned off
    for this client, by whom, and why."""
    if not reason or not reason.strip():
        raise ValueError("A reason is required to delete a test rule.")
    old_status = rule.status
    rule.status = "deleted"
    rule.deleted_reason = reason
    rule.deleted_by = deleted_by_user_id
    rule.deleted_at = datetime.now(timezone.utc)
    log_action(
        db,
        action=f"Deleted test rule '{rule.rule_name}': {reason}",
        organization_id=organization_id,
        user_id=deleted_by_user_id,
        entity_type="test_rules",
        entity_id=rule.rule_id,
        old_value={"status": old_status},
        new_value={"status": "deleted", "reason": reason},
    )
    db.commit()
    db.refresh(rule)
    return rule
