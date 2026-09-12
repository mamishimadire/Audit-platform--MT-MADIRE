import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.core.canonical_model import CANONICAL_MODEL
from app.db.session import get_db
from app.models.audit_test import AuditTest, TestRule
from app.models.rbac import User
from app.schemas.audit_engine import RuleDeleteRequest, RuleRejectRequest, TestRuleCreate, TestRuleOut
from app.services.test_rule_service import (
    approve_rule,
    create_test_rule,
    delete_test_rule,
    generate_rule_from_template,
    list_test_rules,
    reject_rule,
    update_test_rule,
)

router = APIRouter(tags=["test-rules"])


@router.get("/canonical-model/objects", response_model=list[str])
def list_canonical_objects(user: User = Depends(get_current_user)) -> list[str]:
    """Every canonical object a manually-written rule can reference — kept
    live from app.core.canonical_model rather than a second, hand-maintained
    list on the frontend, so the two can never drift out of sync the way
    the frontend's old hardcoded 10-object list already had by the time the
    model grew to cover the full 157-control library."""
    return sorted(CANONICAL_MODEL.keys())


def _get_test_or_404(db: Session, audit_test_id: uuid.UUID) -> AuditTest:
    test = db.get(AuditTest, audit_test_id)
    if test is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit test not found")
    return test


def _to_out(rule: TestRule) -> TestRuleOut:
    return TestRuleOut(
        rule_id=rule.rule_id,
        audit_test_id=rule.audit_test_id,
        rule_name=rule.rule_name,
        rule_type=rule.rule_type,
        rule_definition=json.loads(rule.rule_definition),
        severity=rule.severity,
        status=rule.status,
        origin=rule.origin,
        template_id=rule.template_id,
        created_by=rule.created_by,
        edited_by=rule.edited_by,
        edited_at=rule.edited_at,
        needs_review=rule.needs_review,
        deleted_reason=rule.deleted_reason,
        approved_by=rule.approved_by,
        approved_at=rule.approved_at,
        rejected_reason=rule.rejected_reason,
        version=rule.version,
        supersedes_rule_id=rule.supersedes_rule_id,
    )


def _get_rule_with_org(db: Session, rule_id: uuid.UUID) -> tuple[TestRule, uuid.UUID]:
    rule = db.get(TestRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test rule not found")
    test = db.get(AuditTest, rule.audit_test_id)
    return rule, test.organization_id


@router.post(
    "/organizations/{organization_id}/audit-tests/{audit_test_id}/test-rules",
    response_model=TestRuleOut,
    status_code=status.HTTP_201_CREATED,
)
def create(
    organization_id: uuid.UUID,
    audit_test_id: uuid.UUID,
    payload: TestRuleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> TestRuleOut:
    enforce_same_organization(organization_id, user, db)
    test = _get_test_or_404(db, audit_test_id)
    if test.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit test not found in this organization")
    rule = create_test_rule(db, audit_test_id=audit_test_id, payload=payload, organization_id=organization_id, created_by_user_id=user.user_id)
    return _to_out(rule)


@router.get("/organizations/{organization_id}/audit-tests/{audit_test_id}/test-rules", response_model=list[TestRuleOut])
def list_all(
    organization_id: uuid.UUID, audit_test_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[TestRuleOut]:
    enforce_same_organization(organization_id, user, db)
    return [_to_out(r) for r in list_test_rules(db, audit_test_id=audit_test_id)]


@router.post(
    "/organizations/{organization_id}/audit-tests/{audit_test_id}/test-rules/generate-from-template",
    response_model=TestRuleOut,
    status_code=status.HTTP_201_CREATED,
)
def generate_from_template(
    organization_id: uuid.UUID,
    audit_test_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> TestRuleOut:
    enforce_same_organization(organization_id, user, db)
    test = _get_test_or_404(db, audit_test_id)
    if test.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit test not found in this organization")
    try:
        rule = generate_rule_from_template(db, audit_test_id=audit_test_id, organization_id=organization_id, created_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(rule)


@router.patch("/test-rules/{rule_id}", response_model=TestRuleOut)
def update(
    rule_id: uuid.UUID,
    payload: TestRuleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> TestRuleOut:
    rule, organization_id = _get_rule_with_org(db, rule_id)
    enforce_same_organization(organization_id, user, db)
    updated = update_test_rule(db, rule=rule, payload=payload, organization_id=organization_id, updated_by_user_id=user.user_id)
    return _to_out(updated)


@router.post("/test-rules/{rule_id}/approve", response_model=TestRuleOut)
def approve(
    rule_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> TestRuleOut:
    rule, organization_id = _get_rule_with_org(db, rule_id)
    enforce_same_organization(organization_id, user, db)
    try:
        approved = approve_rule(db, rule=rule, approved_by_user_id=user.user_id, organization_id=organization_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return _to_out(approved)


@router.post("/test-rules/{rule_id}/reject", response_model=TestRuleOut)
def reject(
    rule_id: uuid.UUID,
    payload: RuleRejectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> TestRuleOut:
    rule, organization_id = _get_rule_with_org(db, rule_id)
    enforce_same_organization(organization_id, user, db)
    try:
        rejected = reject_rule(db, rule=rule, reason=payload.reason, rejected_by_user_id=user.user_id, organization_id=organization_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(rejected)


@router.delete("/test-rules/{rule_id}", response_model=TestRuleOut)
def delete(
    rule_id: uuid.UUID,
    payload: RuleDeleteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> TestRuleOut:
    rule, organization_id = _get_rule_with_org(db, rule_id)
    enforce_same_organization(organization_id, user, db)
    try:
        deleted = delete_test_rule(db, rule=rule, reason=payload.reason, organization_id=organization_id, deleted_by_user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(deleted)
