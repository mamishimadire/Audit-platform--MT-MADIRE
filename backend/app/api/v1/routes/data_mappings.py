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
    TestDataMappingCreate,
    TestDataMappingOut,
    TestDataMappingUpdate,
)
from app.services.mapping_service import (
    approve_mapping,
    create_mapping,
    delete_mapping,
    get_mapping_readiness,
    list_mappings,
    reject_mapping,
    suggest_mappings_for_entity,
    update_mapping_field,
)

router = APIRouter(tags=["data-mappings"])


@router.get("/data-sources/entities/{entity_id}/mapping-suggestions", response_model=list[MappingSuggestion])
def suggestions(entity_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Read-only preview — nothing is persisted until a mapping is explicitly created."""
    entity = db.get(DataEntity, entity_id)
    if entity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    source = db.get(DataSource, entity.data_source_id)
    enforce_same_organization(source.organization_id, user, db)
    return suggest_mappings_for_entity(db, entity_id=entity_id)


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
