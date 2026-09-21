import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.join_requirements import join_requirements_for, required_join_key_fields
from app.schemas.test_rule import MissingMatchRule, required_fields_by_object_for, required_objects_for
from app.services import rule_evaluation
from app.services.relationship_validation_service import required_joins_for

FIXTURES = Path(__file__).parent / "fixtures" / "rule_parity.json"
CASES = json.loads(FIXTURES.read_text(encoding="utf-8"))

API002 = CASES[0]["rule"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_platform_engine_matches_the_shared_fixture(case):
    """The Gateway's pandas engine runs the very same file (gateway/tests/test_rule_parity.py),
    so the two engines cannot drift."""
    result = rule_evaluation.evaluate(case["rule"], case["records"])
    assert result.records_analyzed == case["expected"]["records_analyzed"]
    assert sorted(e["exception_data"]["api_id"] for e in result.exceptions) == case["expected"]["flagged"]


def test_bridge_requires_all_three_fields_and_an_explicit_secondary_key():
    base = {"primary_object": "api_access", "secondary_object": "user", "join_field": "user_id"}
    with pytest.raises(ValidationError, match="must be set together"):
        MissingMatchRule(**base, bridge_object="user_roles")
    with pytest.raises(ValidationError, match="secondary_join_field must be set"):
        MissingMatchRule(**base, bridge_object="user_roles", bridge_join_field="user_id", bridge_secondary_join_field="user_id")
    with pytest.raises(ValidationError, match="bridge_condition needs bridge_object"):
        MissingMatchRule(**base, bridge_condition={"field": "x", "operator": "eq", "value": 1})
    ok = MissingMatchRule(
        **base, secondary_join_field="user_id", bridge_object="user_roles", bridge_join_field="user_id", bridge_secondary_join_field="user_id"
    )
    assert ok.bridge_object == "user_roles"


def test_a_half_configured_gate_is_now_rejected_instead_of_silently_skipped():
    base = {"primary_object": "a", "secondary_object": "b", "join_field": "k"}
    with pytest.raises(ValidationError, match="gate_object and gate_join_field"):
        MissingMatchRule(**base, gate_object="c")
    with pytest.raises(ValidationError, match="gate_condition needs gate_object"):
        MissingMatchRule(**base, gate_condition={"field": "x", "operator": "eq", "value": 1})


def test_old_rule_json_without_a_bridge_is_still_valid():
    MissingMatchRule(primary_object="a", secondary_object="b", join_field="k")


def test_bridge_fields_and_objects_are_required_for_mapping_readiness():
    assert required_objects_for(API002) == {"api_access", "user_roles", "user"}
    assert required_fields_by_object_for(API002) == {
        "api_access": {"user_id"},
        "user_roles": {"user_id"},
        "user": {"user_id", "status"},
    }


def test_a_bridge_is_two_joins_to_prove_not_one():
    joins = join_requirements_for(API002)
    assert [(j.left, j.right) for j in joins] == [
        ("api_access.user_id", "user_roles.user_id"),
        ("user_roles.user_id", "user.user_id"),
    ]
    assert required_join_key_fields(API002) == {"api_access": {"user_id"}, "user_roles": {"user_id"}, "user": {"user_id"}}
    # ...and the older validation endpoint sees exactly the same joins now.
    assert required_joins_for(API002) == [
        ("api_access", "user_roles", "user_id", "user_id"),
        ("user_roles", "user", "user_id", "user_id"),
    ]


def test_the_secondary_key_does_not_silently_default_to_the_primary_join_field():
    """With a bridge, join_field names a column on primary; the secondary's key must be stated."""
    rule = {**API002}
    del rule["secondary_join_field"]
    with pytest.raises(KeyError):
        rule_evaluation.evaluate(rule, CASES[0]["records"])  # surfaces upstream as mapping_required, never a wrong answer


def test_the_api002_migration_installs_a_template_the_schema_accepts():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0082_api002_bridge_and_relationship_state.py"
    spec = importlib.util.spec_from_file_location("migration_0082_under_test", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration._NEW_DEFINITION == API002  # the rule the engines are tested with IS the shipped template
    MissingMatchRule(**{k: v for k, v in migration._NEW_DEFINITION.items() if k != "rule_type"})


def test_exception_wording_mentions_the_bridge():
    from app.services import exception_service

    sentence = exception_service._natural_summary(API002, {"user_id": "U3", "api_id": "A3"}, "U3", "API-002")
    assert sentence is not None and "(through user role" in sentence.lower()
    assert "active" in sentence


def test_the_scheduler_can_be_switched_off_for_any_local_server(monkeypatch):
    """A local server pointed at the shared database must never run the production scheduler loop."""
    import sys

    from app import main

    real_modules = dict(sys.modules)
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    monkeypatch.delenv("MT_AUDIT_DISABLE_SCHEDULER", raising=False)
    assert main._scheduler_enabled() is True  # a normal server runs it
    monkeypatch.setenv("MT_AUDIT_DISABLE_SCHEDULER", "1")
    assert main._scheduler_enabled() is False
    monkeypatch.setenv("MT_AUDIT_DISABLE_SCHEDULER", "0")
    assert main._scheduler_enabled() is True
    sys.modules.update({k: v for k, v in real_modules.items() if k not in sys.modules})
    monkeypatch.undo()
    assert main._scheduler_enabled() is False  # under pytest: never
