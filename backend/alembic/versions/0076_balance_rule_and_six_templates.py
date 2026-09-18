"""Adds BalanceRule — a new rule-engine primitive for the standard
"do the debits equal the credits" accounting check — and templates 6 of
the reported gap: API-002, BK-002, GL-002, GL-006, GL-009, NW-004.

=== BalanceRule ===
None of the existing primitives (threshold, duplicate, missing_match,
cross_match_condition, three/four_way_match) aggregate — they all
compare, join, or count rows, never SUM a numeric field across a group.
GL-009 "Debits and credits must balance" needs exactly that: group
journal_lines by journal_id, sum debit and sum credit per journal, flag
every line in a journal where the two totals don't match (within a
floating-point tolerance). app/schemas/test_rule.py's BalanceRule
(object, group_by, debit_field, credit_field, tolerance), implemented in
both engines (app/services/rule_evaluation.py: per-key running totals;
gateway/gateway/rule_engine.py: groupby().agg(sum)), with the matching
plain-English preview/summary/remediation branches everywhere three_way_
match etc. have one. Verified against both engines directly: a balanced
journal (100 debit / 100 credit) does not flag; one off by 10 flags both
of its lines; both engines produce identical output.

=== The 6 templates ===
Each uses a safe, defensible proxy rather than inventing a business rule
this system has no way to know (e.g. GL-002's "authorised" doesn't
enumerate which ROLE NAMES may post journals — that's a client-specific
list nobody here can supply — so it checks the one thing every org
agrees on: the preparer must be a currently active user at all):

- API-002 "API access must be authorised": missing_match, api_access
  grants with no matching ACTIVE user.
- BK-002 "Backups must run according to schedule" / "Identify missed
  backups": missing_match, a scheduled system with no backup_jobs row
  at all.
- GL-002 "Journals must be posted by authorised users" / "Compare
  preparer to user access": missing_match, journal_entries.prepared_by
  with no matching ACTIVE user.
- GL-006 "Unusual journals should be investigated" / "Apply amount/
  date/account/user rules": threshold on journal_entries.amount, using
  a new org-tunable parameter (unusual_journal_amount_threshold,
  default 100000 — see rule_parameter_service.DEFAULT_PARAMETERS) so a
  client's own risk appetite can change the number without editing the
  rule.
- GL-009 "Debits and credits must balance": the new BalanceRule.
- NW-004 "Network devices must be inventoried" / "Compare discovered
  devices to inventory": missing_match, network_devices with no
  matching asset_register row (device_id <-> asset_id, mirroring
  WF-010's existing asset_register <-> web_filter_status pairing by the
  same convention).

=== Deliberately NOT templated here (need a design decision, not a guess) ===
- EN-004 / NW-003: system_configurations/firewall_rules vs
  security_baselines has no shared join key at all (security_baselines
  is a flat, name-keyed lookup — "control", not "system_id"). Every
  existing primitive requires an actual matching key on both sides;
  this needs a new "compare every row to ONE filtered lookup row"
  shape, not a guess at a key that doesn't exist.
- CM-006: change_requests.scheduled_window's real shape (a fixed
  category like CM-003's "emergency", an actual date/time range, or
  something else) isn't established anywhere else in this codebase —
  guessing wrong would template a rule that quietly never means what
  the control's name says.
- GL-010: ap_transactions/ar_transactions/inventory/payroll have no
  field tying a transaction back to a specific general_ledger.account
  at all — reconciling GL to subledger needs that link to exist in the
  canonical model first (a real data-model addition, not just a rule).

Final rule-template coverage: 130/157 (was 124/157 after migration
0073; migrations 0074/0075 fixed existing templates without adding new
coverage).

Revision ID: 0076
Revises: 0075
Create Date: 2026-09-18
"""
import json

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "API-002",
        "API access grant belongs to an inactive or missing user",
        {
            "rule_type": "missing_match",
            "primary_object": "api_access",
            "secondary_object": "user",
            "join_field": "user_id",
            "secondary_condition": {"field": "status", "operator": "eq", "value": "active"},
        },
    ),
    (
        "BK-002",
        "Scheduled system has no recorded backup job",
        {
            "rule_type": "missing_match",
            "primary_object": "backup_schedules",
            "secondary_object": "backup_jobs",
            "join_field": "system_id",
        },
    ),
    (
        "GL-002",
        "Journal entry prepared by an inactive or missing user",
        {
            "rule_type": "missing_match",
            "primary_object": "journal_entries",
            "secondary_object": "user",
            "join_field": "prepared_by",
            "secondary_join_field": "user_id",
            "secondary_condition": {"field": "status", "operator": "eq", "value": "active"},
        },
    ),
    (
        "GL-006",
        "Journal amount exceeds the unusual-amount threshold",
        {
            "rule_type": "threshold",
            "object": "journal_entries",
            "field": "amount",
            "operator": "gt",
            "value": {"kind": "parameter", "key": "unusual_journal_amount_threshold", "default": 100000, "multiplier": 1},
        },
    ),
    (
        "GL-009",
        "Journal debits and credits do not balance",
        {
            "rule_type": "balance",
            "object": "journal_lines",
            "group_by": ["journal_id"],
            "debit_field": "debit",
            "credit_field": "credit",
        },
    ),
    (
        "NW-004",
        "Discovered network device has no inventory record",
        {
            "rule_type": "missing_match",
            "primary_object": "network_devices",
            "secondary_object": "asset_register",
            "join_field": "device_id",
            "secondary_join_field": "asset_id",
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
