"""
Configurable rule parameters — the spec's "never hard-code a threshold
into a rule" principle. An org-level tunable number (dormancy_days, an
access-review SLA, an approval limit...) that a rule references by NAME
instead of by a fixed literal, so a client's own risk appetite can change
the number without anyone editing or regenerating the rule itself.

Stored as one JSON blob in the existing organization_settings table, same
pattern as device_policy_service's device_compliance_policy — a handful of
named numbers with no independent lifecycle, so a dedicated table would be
pure ceremony.

Resolution happens once, on the platform side, right before a due test is
handed to either rule engine (see execution_service.resolve_due_tests_for_
gateway and direct_execution_service._resolve_due_direct_tests) — so
rule_evaluation.py and gateway/gateway/rule_engine.py never need to know
parameters exist at all; by the time they see a rule_definition, every
ParameterReference has already become a plain number.
"""
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.organization import OrganizationSetting
from app.services.audit_log_service import log_action

PARAMETER_SETTING_NAME = "rule_parameters"

# Every parameter a shipped rule template can reference, with the value it
# behaves as until an organization overrides it. Always a plain positive
# magnitude, even for "days in the past" parameters — a template supplies
# its own sign via ParameterReference.multiplier, so this dict (and the
# settings UI reading it) never has to show a negative day-count. Values
# for dormancy_days and access_review_sla_days match what AC-003/AC-006
# already hard-coded before being converted to reference these (see
# migration 0055) — converting a template to use a parameter must never
# silently change what it does for an org that has never touched settings.
DEFAULT_PARAMETERS: dict[str, float] = {
    "dormancy_days": 180,
    "password_expiry_days": 90,
    "access_review_sla_days": 180,
    "termination_deprovision_sla_days": 1,
    "certificate_expiry_warning_days": 30,
    "patch_deployment_sla_days": 30,
    "finding_remediation_sla_days": 60,
    "generic_approval_limit": 10000,
}


def get_parameters(db: Session, *, organization_id: uuid.UUID) -> dict[str, float]:
    setting = db.scalar(
        select(OrganizationSetting).where(
            OrganizationSetting.organization_id == organization_id,
            OrganizationSetting.setting_name == PARAMETER_SETTING_NAME,
        )
    )
    if setting is None or not setting.setting_value:
        return dict(DEFAULT_PARAMETERS)
    try:
        stored = json.loads(setting.setting_value)
    except (json.JSONDecodeError, TypeError):
        return dict(DEFAULT_PARAMETERS)
    # Merge over the defaults so a parameter added to DEFAULT_PARAMETERS
    # after this org last saved its overrides still resolves to something,
    # instead of a KeyError the first time a newer template references it.
    return {**DEFAULT_PARAMETERS, **stored}


def set_parameters(
    db: Session, *, organization_id: uuid.UUID, updates: dict[str, float], updated_by_user_id: uuid.UUID
) -> dict[str, float]:
    current = get_parameters(db, organization_id=organization_id)
    merged = {**current, **updates}
    setting = db.scalar(
        select(OrganizationSetting).where(
            OrganizationSetting.organization_id == organization_id,
            OrganizationSetting.setting_name == PARAMETER_SETTING_NAME,
        )
    )
    value = json.dumps(merged, sort_keys=True)
    if setting is None:
        setting = OrganizationSetting(organization_id=organization_id, setting_name=PARAMETER_SETTING_NAME, setting_value=value)
        db.add(setting)
    else:
        setting.setting_value = value
    db.flush()

    log_action(
        db,
        action="Rule parameters updated",
        organization_id=organization_id,
        user_id=updated_by_user_id,
        entity_type="organization_settings",
        entity_id=setting.setting_id,
        new_value=updates,
    )
    db.commit()
    return merged


def _resolve_value(value: Any, parameters: dict[str, float]) -> Any:
    if isinstance(value, dict):
        if value.get("kind") == "parameter":
            magnitude = parameters.get(value["key"], value.get("default"))
            return magnitude * value.get("multiplier", 1)
        return {k: _resolve_value(v, parameters) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(v, parameters) for v in value]
    return value


def resolve_parameters(rule_definition: dict, parameters: dict[str, float]) -> dict:
    """Walks a rule_definition dict (already loaded from JSON, not yet a
    pydantic model) and replaces every {"kind": "parameter", "key": ...}
    reference with the concrete number this organization currently has for
    that key — falling back to the reference's own "default" if the org
    has never overridden it. Generic over the whole structure rather than
    rule-type-specific, so a future rule primitive that puts a
    ParameterReference somewhere new is resolved correctly without this
    function needing to change."""
    return _resolve_value(rule_definition, parameters)
