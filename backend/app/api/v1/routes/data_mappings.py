import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.audit_test import AuditTest, TestDataMapping
from app.models.data_source import DataEntity, DataSource
from app.models.rbac import User
from app.schemas.data_mapping import (
    MappingReadinessOut,
    MappingRejectRequest,
    MappingSuggestion,
    JoinReportOut,
    RelationshipCheckOut,
    RulePreviewOut,
    TestDataMappingCreate,
    TestDataMappingOut,
    TestDataMappingUpdate,
)
from app.services.mapping_service import (
    approve_mapping,
    create_mapping,
    delete_mapping,
    get_mapping_readiness,
    get_template_requirements,
    list_mappings,
    reject_mapping,
    suggest_mappings_for_entity,
    update_mapping_field,
)
from app.services.join_resolution_service import build_join_report
from app.services.relationship_validation_service import validate_relationships
from app.services.rule_preview_service import build_rule_preview
from app.services.test_rule_service import get_control_rule_template, list_test_rules

router = APIRouter(tags=["data-mappings"])


@router.get("/data-sources/entities/{entity_id}/mapping-suggestions", response_model=list[MappingSuggestion])
def suggestions(
    entity_id: uuid.UUID,
    audit_test_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Read-only preview — nothing is persisted until a mapping is explicitly created.
    With audit_test_id, columns that serve as a join key for that test's control
    are also judged by whether the relationship they carry actually holds."""
    entity = db.get(DataEntity, entity_id)
    if entity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    source = db.get(DataSource, entity.data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    return suggest_mappings_for_entity(db, entity_id=entity_id, audit_test_id=audit_test_id)


def _get_test_or_404(db: Session, audit_test_id: uuid.UUID) -> AuditTest:
    test = db.get(AuditTest, audit_test_id)
    if test is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit test not found")
    return test


@router.post(
    "/organizations/{organization_id}/audit-tests/{audit_test_id}/data-mappings",
    response_model=TestDataMappingOut,
    status_code=status.HTTP_201_CREATED,
)
def create(
    organization_id: uuid.UUID,
    audit_test_id: uuid.UUID,
    payload: TestDataMappingCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> TestDataMapping:
    enforce_same_organization(organization_id, user, db)
    test = _get_test_or_404(db, audit_test_id)
    if test.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit test not found in this organization")
    return create_mapping(
        db, audit_test_id=audit_test_id, payload=payload, organization_id=organization_id, created_by_user_id=user.user_id
    )


@router.get("/organizations/{organization_id}/audit-tests/{audit_test_id}/data-mappings", response_model=list[TestDataMappingOut])
def list_all(
    organization_id: uuid.UUID, audit_test_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[TestDataMapping]:
    enforce_same_organization(organization_id, user, db)
    test = _get_test_or_404(db, audit_test_id)
    if test.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit test not found in this organization")
    return list_mappings(db, audit_test_id=audit_test_id)


@router.get(
    "/organizations/{organization_id}/audit-tests/{audit_test_id}/join-resolution",
    response_model=JoinReportOut,
)
def join_resolution(
    organization_id: uuid.UUID, audit_test_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> JoinReportOut:
    """How each join this control's rule needs is satisfied by the client's actual
    tables (which columns, how strongly the schema and the data back it up),
    plus how the control's tables connect through any bridge tables. Reads stored
    relationship evidence only; refresh it from Data Sources."""
    enforce_same_organization(organization_id, user, db)
    test = _get_test_or_404(db, audit_test_id)
    if test.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit test not found in this organization")
    return build_join_report(db, audit_test_id=audit_test_id)


@router.get(
    "/organizations/{organization_id}/audit-tests/{audit_test_id}/mapping-readiness", response_model=MappingReadinessOut
)
def readiness(
    organization_id: uuid.UUID, audit_test_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> MappingReadinessOut:
    enforce_same_organization(organization_id, user, db)
    test = _get_test_or_404(db, audit_test_id)
    if test.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit test not found in this organization")
    return get_mapping_readiness(db, audit_test_id=audit_test_id)


@router.get(
    "/organizations/{organization_id}/audit-tests/{audit_test_id}/template-requirements", response_model=MappingReadinessOut
)
def template_requirements(
    organization_id: uuid.UUID, audit_test_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> MappingReadinessOut:
    """What this control's rule template actually needs, independent of
    whether a rule has been generated yet — lets the mapping screen show
    "required for this test" before Generate from control template is
    even clicked. Empty objects (has_rule=False) means this control has no
    template, so there's nothing to narrow the screen down to."""
    enforce_same_organization(organization_id, user, db)
    test = _get_test_or_404(db, audit_test_id)
    if test.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit test not found in this organization")
    return get_template_requirements(db, audit_test_id=audit_test_id)


@router.post(
    "/organizations/{organization_id}/audit-tests/{audit_test_id}/relationship-validation",
    response_model=list[RelationshipCheckOut],
)
def relationship_validation(
    organization_id: uuid.UUID, audit_test_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[RelationshipCheckOut]:
    """Live query against the client's own data (direct connections only)
    to check whether a join_field mapped correctly by name actually shares
    values across both sides — see relationship_validation_service. Uses
    whichever rule already exists to test against: the active rule if one
    has been approved, else the control's own template (so this can run
    before a rule is even generated)."""
    enforce_same_organization(organization_id, user, db)
    test = _get_test_or_404(db, audit_test_id)
    if test.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit test not found in this organization")

    active_rules = [r for r in list_test_rules(db, audit_test_id=audit_test_id) if r.status == "active"]
    if active_rules:
        rule_definition = json.loads(active_rules[0].rule_definition)
    else:
        _, template = get_control_rule_template(db, audit_test_id=audit_test_id)
        if template is None:
            return []
        rule_definition = json.loads(template.rule_definition)
    return validate_relationships(db, audit_test_id=audit_test_id, rule_definition=rule_definition)


@router.get(
    "/organizations/{organization_id}/audit-tests/{audit_test_id}/rule-preview",
    response_model=RulePreviewOut,
)
def rule_preview(
    organization_id: uuid.UUID, audit_test_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """Plain-English SOURCE/JOIN/FILTER/TEST/PASS breakdown of whichever
    rule is most relevant right now: the active rule if one is approved,
    else a pending one awaiting approval, else the control's own template
    — so this can be shown before a rule even exists, letting an auditor
    see what WOULD be tested before clicking "Generate from control
    template" at all."""
    enforce_same_organization(organization_id, user, db)
    test = _get_test_or_404(db, audit_test_id)
    if test.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit test not found in this organization")

    rules = list_test_rules(db, audit_test_id=audit_test_id)
    rule = next((r for r in rules if r.status == "active"), None) or next((r for r in rules if r.status == "pending_approval"), None)
    if rule is not None:
        rule_definition = json.loads(rule.rule_definition)
    else:
        _, template = get_control_rule_template(db, audit_test_id=audit_test_id)
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No rule or control template exists yet for this test")
        rule_definition = json.loads(template.rule_definition)
    return build_rule_preview(db, audit_test_id=audit_test_id, rule_definition=rule_definition)


def _get_mapping_with_org(db: Session, mapping_id: uuid.UUID) -> tuple[TestDataMapping, uuid.UUID]:
    mapping = db.get(TestDataMapping, mapping_id)
    if mapping is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mapping not found")
    test = db.get(AuditTest, mapping.audit_test_id)
    return mapping, test.organization_id


@router.patch("/data-mappings/{mapping_id}", response_model=TestDataMappingOut)
def update(
    mapping_id: uuid.UUID,
    payload: TestDataMappingUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> TestDataMapping:
    mapping, organization_id = _get_mapping_with_org(db, mapping_id)
    enforce_same_organization(organization_id, user, db)
    return update_mapping_field(db, mapping=mapping, canonical_field=payload.canonical_field, updated_by_user_id=user.user_id)


@router.delete("/data-mappings/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(
    mapping_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_permissions("audit_framework:manage"))
) -> None:
    mapping, organization_id = _get_mapping_with_org(db, mapping_id)
    enforce_same_organization(organization_id, user, db)
    delete_mapping(db, mapping=mapping, organization_id=organization_id, deleted_by_user_id=user.user_id)


@router.post("/data-mappings/{mapping_id}/approve", response_model=TestDataMappingOut)
def approve(
    mapping_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_permissions("audit_framework:manage"))
) -> TestDataMapping:
    mapping, organization_id = _get_mapping_with_org(db, mapping_id)
    enforce_same_organization(organization_id, user, db)
    try:
        return approve_mapping(db, mapping=mapping, approved_by_user_id=user.user_id, organization_id=organization_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post("/data-mappings/{mapping_id}/reject", response_model=TestDataMappingOut)
def reject(
    mapping_id: uuid.UUID, payload: MappingRejectRequest, db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> TestDataMapping:
    mapping, organization_id = _get_mapping_with_org(db, mapping_id)
    enforce_same_organization(organization_id, user, db)
    try:
        return reject_mapping(db, mapping=mapping, reason=payload.reason, rejected_by_user_id=user.user_id, organization_id=organization_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
