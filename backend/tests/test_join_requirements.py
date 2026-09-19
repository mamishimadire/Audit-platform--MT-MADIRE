import json

import pytest
from sqlalchemy import select

from app.core.canonical_model import CANONICAL_MODEL
from app.core.join_requirements import (
    JoinRequirement,
    join_requirements_for,
    reference_role,
    reference_roles_agree,
    required_join_key_fields,
)
from app.models.control_library import ControlRuleTemplate


def test_missing_match_default_and_explicit_secondary_key():
    same = join_requirements_for(
        {"rule_type": "missing_match", "primary_object": "api_access", "secondary_object": "user", "join_field": "user_id"}
    )
    assert same == [JoinRequirement("missing_match", "api_access", "user_id", "user", "user_id")]
    differing = join_requirements_for(
        {"rule_type": "missing_match", "primary_object": "journal_entries", "secondary_object": "user",
         "join_field": "prepared_by", "secondary_join_field": "user_id"}
    )
    assert differing[0].left == "journal_entries.prepared_by" and differing[0].right == "user.user_id"


def test_gate_join_is_a_second_requirement():
    joins = join_requirements_for(
        {"rule_type": "missing_match", "primary_object": "remote_access_logs", "secondary_object": "privileged_access_approvals",
         "join_field": "session_id", "gate_object": "user_roles", "gate_join_field": "user_id"}
    )
    assert [(j.left, j.right) for j in joins] == [
        ("remote_access_logs.session_id", "privileged_access_approvals.session_id"),
        ("remote_access_logs.user_id", "user_roles.user_id"),
    ]


def test_chained_rules_yield_one_requirement_per_hop():
    three = join_requirements_for(
        {"rule_type": "three_way_match", "primary_object": "purchase_orders", "secondary_object": "goods_receipts",
         "tertiary_object": "supplier_invoices", "join_field_primary_secondary": "po_number", "join_field_secondary_tertiary": "po_number"}
    )
    assert len(three) == 2 and three[1].left_object == "goods_receipts"
    four = join_requirements_for(
        {"rule_type": "four_way_match", "primary_object": "payments", "secondary_object": "payment_approvals",
         "tertiary_object": "user_roles", "quaternary_object": "approval_limits",
         "join_field_primary_secondary": "payment_id", "join_field_secondary_tertiary": "approved_by",
         "secondary_join_field_1": None, "tertiary_join_field": "user_id",
         "join_field_tertiary_quaternary": "role", "quaternary_join_field": "role"}
    )
    assert [(j.left, j.right) for j in four] == [
        ("payments.payment_id", "payment_approvals.payment_id"),
        ("payment_approvals.approved_by", "user_roles.user_id"),
        ("user_roles.role", "approval_limits.role"),
    ]


def test_reconciliation_and_rules_without_a_join():
    rec = join_requirements_for(
        {"rule_type": "reconciliation", "ledger_object": "general_ledger", "ledger_key_field": "account",
         "subledger_object": "ap_transactions", "subledger_key_field": "account"}
    )
    assert len(rec) == 1
    for rule in (
        {"rule_type": "threshold", "object": "x", "field": "y"},
        {"rule_type": "conflict_matrix", "role_permission_object": "a", "rules_object": "b"},
        {"rule_type": "baseline_comparison", "object": "a", "baseline_object": "b"},
        {},
    ):
        assert join_requirements_for(rule) == []


def test_self_join_is_not_a_relationship():
    assert join_requirements_for(
        {"rule_type": "cross_match_condition", "primary_object": "data_access", "secondary_object": "data_access", "join_field": "access_id"}
    ) == []


def test_required_join_key_fields_groups_by_object():
    keys = required_join_key_fields(
        {"rule_type": "missing_match", "primary_object": "api_access", "secondary_object": "user", "join_field": "user_id"}
    )
    assert keys == {"api_access": {"user_id"}, "user": {"user_id"}}


@pytest.mark.parametrize(
    "required,column,expected",
    [
        ("user_id", "user_id", "match"),
        ("user_id", "created_by", "mismatch"),
        ("user_id", "approved_by", "mismatch"),
        ("user_id", "owner_id", "mismatch"),
        ("prepared_by", "prepared_by", "match"),
        ("prepared_by", "approved_by", "mismatch"),
        ("prepared_by", "user_id", "mismatch"),
        ("user_id", "employee_id", "match"),  # both plain identity keys: the role agrees (user vs employee is a value question)
        ("system_id", "asset_name", "neutral"),
    ],
)
def test_reference_roles(required, column, expected):
    assert reference_roles_agree(required, column) == expected


def test_reference_role_labels():
    assert reference_role("created_by") == "actor:created"
    assert reference_role("owner_id") == "actor:owner"
    assert reference_role("user_id") == "subject"
    assert reference_role("description") is None


def test_every_rule_template_in_the_library_yields_valid_requirements(db):
    """Whatever the primitive, every requirement must name a canonical object
    and field that actually exist, or resolution could never find its columns."""
    templates = list(db.scalars(select(ControlRuleTemplate)))
    assert len(templates) >= 150
    total = joined = 0
    for template in templates:
        definition = template.rule_definition
        if isinstance(definition, str):
            definition = json.loads(definition)
        for req in join_requirements_for(definition):
            total += 1
            for obj, field in ((req.left_object, req.left_field), (req.right_object, req.right_field)):
                assert obj in CANONICAL_MODEL, (template.rule_name, obj)
                assert field in CANONICAL_MODEL[obj], (template.rule_name, obj, field)
        if join_requirements_for(definition):
            joined += 1
    assert joined >= 80 and total >= joined
