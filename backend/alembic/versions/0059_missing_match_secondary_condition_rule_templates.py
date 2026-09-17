"""Extend rule-template coverage, round 4, part 1: adds MissingMatchRule.
secondary_condition (see app/schemas/test_rule.py's module docstring and
MissingMatchRule's own docstring for the full design rationale) and
templates the 5 controls it — together with a fresh re-derivation of a
sixth that turned out to need no new capability at all — unblocks.

=== Why this pass exists ===
110 of 157 controls had a rule_definition template after migration 0054.
This project's instruction for every fresh pass is to re-derive the
untemplated list from the live control_library/control_rule_templates
tables rather than trust the prior pass's one-line category guesses, and
to re-examine each control's actual required data (its real seed-fixture
shape, not just its required_tables list) before concluding a capability
gap still applies. Doing exactly that surfaced two controls whose stated
"3-way anti-join" blocker (0052/0054) does not survive contact with the
real data:

  - AC-005 "Privileged access must be authorised": 0052 assumed resolving
    "privileged" required joining user_roles -> roles for role_name, then
    an anti-join against access_requests — a 3-object chain with an
    anti-join at the end, which ThreeWayMatchRule genuinely cannot do
    (inner joins only). But 0052's OWN later analysis of RA-007 (same
    migration, a few paragraphs down) already establishes that user_roles.
    role itself holds usable role-name text directly ("user_roles.role
    even matches the '(?i)admin' pattern... catching both 'it_admin' and
    'system_administrator'") — no join to "roles" is needed at all to test
    "does this role name look privileged." Once that's recognised, AC-005
    collapses from "3-way anti-join" to a plain 2-object missing_match:
    user_roles (primary_condition: role matches an admin/privileged
    pattern) with no match in access_requests. No new capability required
    — this was a mis-assessment in the ORIGINAL 3-way analysis, not a gap
    closed by anything added since. Confirmed against a fixture shaped
    like the platform's own seed data (scripts/seed_mongo_demo.py): U002/
    U007/U011/U013 hold it_admin/system_administrator roles; only U011 has
    an access_requests row (already approved); U005's hr_admin role also
    matches the same industry-standard "privileged role name" pattern
    (admin/administrator/privileged/superuser/root) already established
    for AC-009's generic-account username pattern (0054) — correctly
    flagging U005 alongside U002/U007/U013 is a MORE complete answer than
    the single violation 0052's fixture happened to call out, not a wrong
    one: an HR admin role with no authorisation on file is exactly the
    kind of privileged-without-approval finding this control exists to
    catch. Uses secondary_condition too (status == "approved", not just
    "any row exists") for the same reason AC-001 needs it below — a
    pending, not-yet-approved access_requests row must not count as
    satisfying "authorised."
  - API-002, RA-003, RA-007: re-examined the same way and NOT
    reclassified — API-002's seed-fixture contractor (U012) already HAS a
    user_roles row (ap_clerk) and that role already HAS a role_permissions
    entry (create_supplier_invoice), so a plain 2-object anti-join against
    user_roles would incorrectly clear it; the real gap (no permission
    value anywhere represents "API access" specifically) is a business-
    mapping gap independent of the join depth, not fixed by anything here.
    RA-007 needs an INNER join (session -> privileged role) chained into
    an ANTI-join (session -> approval) — the anti-join is needed at the
    far end of a 3-object chain, which is exactly the shape
    MissingMatchRule's 2-object-only anti-join and ThreeWayMatchRule's
    inner-only join can't jointly express. RA-003 has no seed-fixture
    violation to confirm its real intended shape against (system_users/
    hr_employees/access_requests could mean "no valid employee record" or
    "no approved access request" or both) — guessing would risk exactly
    the "plausible-looking but wrong template" this project's one hard
    constraint forbids. All three remain blocked; see 0060's final
    accounting for the complete, precise reason each is still blocked.

=== The new capability: MissingMatchRule.secondary_condition ===
Filters secondary_object rows BEFORE the anti-join, mirroring
primary_condition on the other side (added in the very first capability
round, 0050). Without it, a missing_match could only express "primary rows
with no matching row in secondary AT ALL" — treating ANY row that merely
shares the join key as satisfying the check, regardless of that row's own
status. Two concrete gaps this closes:
  1. AC-001 "User access must be approved": ARQ003 (access_requests,
     status "pending") shares user_id with U006 (system_users, active) —
     an unfiltered anti-join would wrongly treat the pending request as
     proof of authorisation. secondary_condition = status == "approved"
     fixes this: only an approved request counts as a match, so U006
     (pending only) is still correctly flagged.
  2. MD-005/DP-002/PY-010 "audit_logs polymorphic entity_type/entity_id":
     audit_logs is one shared table logging every entity type in the
     system. entity_id only means "this specific supplier_id" once
     entity_type is filtered to the ONE entity type the primary object
     represents — an unfiltered anti-join could match a supplier_id
     against an entity_id belonging to a completely different entity_type
     purely by coincidence (as the synthetic dry-run below deliberately
     exercises), silently under-reporting violations. secondary_condition
     = entity_type == "<the primary object's own name>" fixes this.

Both engines (app.services.rule_evaluation and gateway.gateway.
rule_engine) were updated identically: secondary_object rows are filtered
by secondary_condition before the set of matched join-key values is built,
so the anti-join membership test only ever considers rows that also
satisfy the secondary condition. required_fields_by_object was updated so
secondary_condition.field is correctly declared as a required field on the
secondary side (merged into the single self-join entry when primary_object
== secondary_object, same collision-avoidance already used for
primary_condition/field_comparison).

=== Validation discipline ===
Every rule_definition below was schema-validated against
app.schemas.test_rule's Pydantic models (TestRuleDefinition, via
pydantic.TypeAdapter) AND dry-run against BOTH engines with synthetic
good/bad rows, confirming each produces exactly the expected exception set
in both — including an adversarial row for MD-005/DP-002/PY-010 sharing
the join-key VALUE but the WRONG entity_type, to specifically exercise the
polymorphic-join-safety fix (see the throwaway script used during this
pass; not checked in, per the existing project convention of ephemeral
validation scripts).

=== Templated in this migration ===
  - AC-001 "User access must be approved": missing_match, system_users
    (primary_condition status == "active") -> access_requests
    (secondary_condition status == "approved"), join_field user_id.
  - AC-005 "Privileged access must be authorised": missing_match,
    user_roles (primary_condition role matches "(?i)(admin|administrator|
    privileged|superuser|root)") -> access_requests (secondary_condition
    status == "approved"), join_field user_id.
  - MD-005 "Supplier master changes must be logged": missing_match,
    suppliers -> audit_logs (secondary_join_field entity_id,
    secondary_condition entity_type == "suppliers"), join_field
    supplier_id.
  - DP-002 "Sensitive data access must be logged": missing_match,
    data_access -> audit_logs (secondary_join_field entity_id,
    secondary_condition entity_type == "data_access"), join_field
    access_id.
  - PY-010 "Payroll changes should be logged": missing_match,
    payroll_changes -> audit_logs (secondary_join_field entity_id,
    secondary_condition entity_type == "payroll_changes"), join_field
    change_id.

Brings coverage from 110/157 to 115/157. See migration 0060 for the
dynamic-relative-date capability (BK-004, OP-007, DP-004, PM-001, VM-002)
and the complete final accounting of every control still blocked.

Revision ID: 0059
Revises: 0058
Create Date: 2026-09-17
"""
import json

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None

_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "AC-001",
        "Active system user with no approved access request on file",
        {
            "rule_type": "missing_match",
            "primary_object": "system_users",
            "secondary_object": "access_requests",
            "join_field": "user_id",
            "primary_condition": {"field": "status", "operator": "eq", "value": "active"},
            "secondary_condition": {"field": "status", "operator": "eq", "value": "approved"},
        },
    ),
    (
        "AC-005",
        "Privileged role holder with no approved access request on file",
        {
            "rule_type": "missing_match",
            "primary_object": "user_roles",
            "secondary_object": "access_requests",
            "join_field": "user_id",
            "primary_condition": {
                "field": "role",
                "operator": "matches",
                "value": "(?i)(admin|administrator|privileged|superuser|root)",
            },
            "secondary_condition": {"field": "status", "operator": "eq", "value": "approved"},
        },
    ),
    (
        "MD-005",
        "Supplier master record with no matching audit log entry",
        {
            "rule_type": "missing_match",
            "primary_object": "suppliers",
            "secondary_object": "audit_logs",
            "join_field": "supplier_id",
            "secondary_join_field": "entity_id",
            "secondary_condition": {"field": "entity_type", "operator": "eq", "value": "suppliers"},
        },
    ),
    (
        "DP-002",
        "Sensitive data access grant with no matching audit log entry",
        {
            "rule_type": "missing_match",
            "primary_object": "data_access",
            "secondary_object": "audit_logs",
            "join_field": "access_id",
            "secondary_join_field": "entity_id",
            "secondary_condition": {"field": "entity_type", "operator": "eq", "value": "data_access"},
        },
    ),
    (
        "PY-010",
        "Payroll change with no matching audit log entry",
        {
            "rule_type": "missing_match",
            "primary_object": "payroll_changes",
            "secondary_object": "audit_logs",
            "join_field": "change_id",
            "secondary_join_field": "entity_id",
            "secondary_condition": {"field": "entity_type", "operator": "eq", "value": "payroll_changes"},
        },
    ),
]


def upgrade() -> None:
    for control_code, rule_name, rule_definition in _TEMPLATES:
        rule_definition_json = json.dumps(rule_definition).replace("'", "''")
        rule_name_escaped = rule_name.replace("'", "''")
        op.execute(
            f"""
            INSERT INTO control_rule_templates (control_library_id, rule_name, rule_definition)
            SELECT control_library_id, '{rule_name_escaped}', '{rule_definition_json}'
            FROM control_library WHERE control_code = '{control_code}'
            ON CONFLICT (control_library_id) DO NOTHING
            """
        )


def downgrade() -> None:
    codes = ", ".join(f"'{code}'" for code, _, _ in _TEMPLATES)
    op.execute(
        f"""
        DELETE FROM control_rule_templates
        WHERE control_library_id IN (SELECT control_library_id FROM control_library WHERE control_code IN ({codes}))
        """
    )
