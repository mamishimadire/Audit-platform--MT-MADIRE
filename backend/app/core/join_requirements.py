"""
What a control's rule needs to JOIN, independent of any client's database.

A rule template says "these canonical objects must line up on these canonical
fields" (api_access.user_id <-> user.user_id). That is the control's half of
the mapping problem, and it is the same for every client. The client's half
(which physical columns satisfy it, and whether they really relate) lives in
the relationship graph — see join_resolution_service.

Every rule primitive that joins objects is covered here (missing_match incl.
its gate join, cross_match_condition, three/four-way chains, reconciliation).
Single-object rules and conflict_matrix/baseline_comparison have no join key.

This module also holds the small vocabulary that stops a column that merely
LOOKS like a user identifier from passing as the subject of a join:
`created_by`, `approved_by`, `owner_id`... all hold user ids, and all mean
something different from `user_id`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JoinRequirement:
    primitive: str
    left_object: str
    left_field: str
    right_object: str
    right_field: str

    @property
    def left(self) -> str:
        return f"{self.left_object}.{self.left_field}"

    @property
    def right(self) -> str:
        return f"{self.right_object}.{self.right_field}"


def _pair(primitive: str, left_object, left_field, right_object, right_field) -> JoinRequirement | None:
    # A self-join (the same object on both sides, e.g. the "critical AND
    # unresolved" trick) is a compound condition on one table, not a
    # relationship between two: a set always matches itself.
    if not (left_object and left_field and right_object and right_field) or left_object == right_object:
        return None
    return JoinRequirement(primitive, left_object, left_field, right_object, right_field)


def join_requirements_for(rule_definition: dict) -> list[JoinRequirement]:
    """Every pairwise join a rule performs, in chain order."""
    rule_type = rule_definition.get("rule_type")
    d = rule_definition
    found: list[JoinRequirement | None] = []

    if rule_type in ("missing_match", "cross_match_condition"):
        if rule_type == "missing_match" and d.get("bridge_object"):
            # primary -> bridge, then bridge -> secondary: two relationships to prove.
            found.append(_pair(rule_type, d.get("primary_object"), d.get("join_field"), d.get("bridge_object"), d.get("bridge_join_field")))
            found.append(
                _pair(rule_type, d.get("bridge_object"), d.get("bridge_secondary_join_field"), d.get("secondary_object"), d.get("secondary_join_field"))
            )
        else:
            found.append(
                _pair(
                    rule_type, d.get("primary_object"), d.get("join_field"),
                    d.get("secondary_object"), d.get("secondary_join_field") or d.get("join_field"),
                )
            )
        if rule_type == "missing_match" and d.get("gate_object") and d.get("gate_join_field"):
            found.append(
                _pair(
                    rule_type, d.get("primary_object"), d.get("gate_join_field"),
                    d.get("gate_object"), d.get("gate_secondary_join_field") or d.get("gate_join_field"),
                )
            )
    elif rule_type in ("three_way_match", "four_way_match"):
        jps = d.get("join_field_primary_secondary")
        found.append(_pair(rule_type, d.get("primary_object"), jps, d.get("secondary_object"), d.get("secondary_join_field_1") or jps))
        jst = d.get("join_field_secondary_tertiary")
        found.append(_pair(rule_type, d.get("secondary_object"), jst, d.get("tertiary_object"), d.get("tertiary_join_field") or jst))
        if rule_type == "four_way_match":
            jtq = d.get("join_field_tertiary_quaternary")
            found.append(_pair(rule_type, d.get("tertiary_object"), jtq, d.get("quaternary_object"), d.get("quaternary_join_field") or jtq))
    elif rule_type == "reconciliation":
        found.append(
            _pair(
                rule_type, d.get("ledger_object"), d.get("ledger_key_field"),
                d.get("subledger_object"), d.get("subledger_key_field"),
            )
        )
    return [j for j in found if j is not None]


def required_join_key_fields(rule_definition: dict) -> dict[str, set[str]]:
    """{canonical_object: {fields that serve as a join key}} — the subset of a
    rule's required fields that identify rows rather than describe them."""
    keys: dict[str, set[str]] = {}
    for j in join_requirements_for(rule_definition):
        keys.setdefault(j.left_object, set()).add(j.left_field)
        keys.setdefault(j.right_object, set()).add(j.right_field)
    return keys


# ---- who a user-reference column refers to --------------------------------
# `user_id` names the SUBJECT (whose access, whose record). `created_by`,
# `approved_by`, `owner_id`... also hold user ids but name an ACTOR in a
# specific role. Joining on the wrong one is the classic silent mapping
# error: the values match, the meaning does not.
_ACTOR_EXACT = {"owner", "owner_id", "assigned_to", "assignee", "assignee_id", "manager_id", "supervisor_id"}


def reference_role(field_name: str) -> str | None:
    """'subject' for plain identity keys, 'actor:<verb>' for a column that names
    who DID something (created_by -> actor:created, owner_id -> actor:owner),
    None when the name says nothing about who it refers to."""
    name = field_name.strip().lower()
    tokens = [t for t in name.replace("-", "_").split("_") if t]
    if not tokens:
        return None
    if name in _ACTOR_EXACT:
        return f"actor:{tokens[0]}"
    if "by" in tokens[1:] or (len(tokens) >= 2 and tokens[-1] == "by"):
        return f"actor:{tokens[0]}"
    if tokens[0] == "owner":
        return "actor:owner"
    if name in {"user_id", "employee_id", "person_id", "account_id", "customer_id", "supplier_id", "vendor_id", "userid"}:
        return "subject"
    if tokens[-1] == "id" and len(tokens) == 2:
        return "subject"
    return None


def reference_roles_agree(required_field: str, column_name: str) -> str:
    """'match' | 'mismatch' | 'neutral' — does this column play the role the
    requirement needs? A requirement for `user_id` is NOT met by `approved_by`;
    a requirement for `prepared_by` (GL-002) IS met by `prepared_by` and not by
    `approved_by`. 'neutral' when either side says nothing about a role."""
    need = reference_role(required_field)
    have = reference_role(column_name)
    if need is None or have is None:
        return "neutral"
    if need == have:
        return "match"
    # A plain identity requirement is never satisfied by an actor column, and an
    # actor requirement is never satisfied by a different actor (approved vs created).
    if have.startswith("actor:") or need.startswith("actor:"):
        return "mismatch"
    return "neutral"
