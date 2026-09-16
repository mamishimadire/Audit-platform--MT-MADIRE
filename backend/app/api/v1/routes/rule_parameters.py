import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.rbac import User
from app.schemas.rule_parameter import RuleParametersOut, RuleParametersUpdate
from app.services.rule_parameter_service import get_parameters, set_parameters

router = APIRouter(tags=["rule-parameters"])


@router.get("/organizations/{organization_id}/rule-parameters", response_model=RuleParametersOut)
def get_rule_parameters(
    organization_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> RuleParametersOut:
    """Every tunable number a shipped rule template can reference (see
    ParameterReference in app/schemas/test_rule.py), and what this org
    currently has each one set to — its own override, or the shipped
    default if it has never changed one."""
    enforce_same_organization(organization_id, user, db)
    return RuleParametersOut(parameters=get_parameters(db, organization_id=organization_id))


@router.put("/organizations/{organization_id}/rule-parameters", response_model=RuleParametersOut)
def update_rule_parameters(
    organization_id: uuid.UUID,
    payload: RuleParametersUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_framework:manage")),
) -> RuleParametersOut:
    """Applies immediately — the next due-test resolution for this org
    (resolve_due_tests_for_gateway / _resolve_due_direct_tests) picks up
    the new value, no rule needs editing or regenerating. Only the keys
    included in the payload change; everything else keeps its current
    value."""
    enforce_same_organization(organization_id, user, db)
    merged = set_parameters(
        db, organization_id=organization_id, updates=payload.parameters, updated_by_user_id=user.user_id
    )
    return RuleParametersOut(parameters=merged)
