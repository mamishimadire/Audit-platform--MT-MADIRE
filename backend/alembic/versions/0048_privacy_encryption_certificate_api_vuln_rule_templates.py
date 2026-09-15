"""Extend rule-template coverage: Data Privacy, Encryption Controls,
Certificate Management, API Controls, and Vulnerability Management.

Same validation discipline as 0044/0046/0047: every rule_definition here
was schema-validated against app.schemas.test_rule AND dry-run against a
copy of the Gateway rule engine with synthetic good/bad rows. See 0046's
docstring for the "boolean self-filter" and "reused cross-object rule"
techniques used again below.

A fourth technique introduced in this migration: for EN-002, the control's
audit_procedure names TWO conditions ("expired/unauthorised keys") but only
one of them (encryption_keys.authorized == False) is expressible with the
current primitives — the other (expiry) needs a relative-date comparison.
Rather than skip the control entirely, or silently return a rule that
LOOKS like it covers the whole audit_procedure but doesn't, the template is
deliberately scoped and named for exactly what it tests ("Encryption key is
marked unauthorised") and the gap for the uncovered half is documented here
and in 0047/0046's skip lists. This is not a new gray area — 0044's GL-003-
style controls and this file's BK-003/BK-005 (0047) already do the same
thing; it's called out again here because it's the clearest single example
of "partial-but-honest is fine, partial-but-mislabeled is not."

=== Templated in this migration ===
Data Privacy:
  - DP-001 "Access to personal information must be authorised": boolean
    self-filter — data_access already carries its own granted_by field;
    flagged via is_null on a self-join keyed by access_id (a genuine
    per-row key). No separate "data_access_approvals" object exists in the
    canonical model, and none is needed — the authorization signal is
    already on the record.
  - DP-003 "Personal information should not be unnecessarily exposed":
    cross_match_condition, user_access (access_level == "full") ->
    data_classification (classification == "personal_info") on dataset.
Encryption Controls:
  - EN-002 "Encryption keys must be managed" (unauthorised-key half only —
    see above): boolean self-filter, encryption_keys.authorized == False,
    self-joined on key_id (a genuine per-row key).
Certificate Management:
  - CERT-003 "Certificates must belong to authorised systems":
    missing_match, certificates -> system on system_id.
  - CERT-004 "Certificate renewals must be tracked": missing_match,
    certificates -> certificate_renewals on cert_id.
API Controls:
  - API-001 "APIs must be registered": boolean self-filter, api_inventory.
    registered == False, self-joined on api_id.
  - API-004 "Sensitive APIs must use encryption": boolean self-filter,
    api_configurations.tls_enabled == False, self-joined on api_id. No
    canonical field distinguishes "sensitive" APIs from others, so this is
    (honestly) scoped to all APIs, matching how OTC-004/API-004-shaped
    controls elsewhere are handled when no subset field exists.
Vulnerability Management:
  - VM-004 "Vulnerability remediation must be retested": missing_match,
    remediation_actions -> retests on vuln_id. Deliberately joined from
    remediation_actions (not the full vulnerabilities table) as the
    primary object — remediation_actions IS already the correctly-scoped
    "things that were remediated and now need a retest" population, so no
    filtering gap applies here the way it does elsewhere.

=== Deliberately NOT templated, with the specific gap ===

Data Privacy (DP-002, DP-004, DP-005):
  - DP-002 "Sensitive data access must be logged": audit_logs' polymorphic
    entity_type/entity_id pair doesn't align with data_access.access_id —
    no shared join_field name (same gap as MD-005/PY-010).
  - DP-004 "Data retention requirements must be followed": needs a
    relative-date window (created_at + retention_rules.retention_days vs
    "now"), compounded by data_records having no "dataset" field to even
    join against retention_rules in the first place.
  - DP-005 "Data deletion requests should be processed": privacy_requests.
    type distinguishes deletion requests from other request types, so an
    unfiltered missing_match against ALL privacy_requests would wrongly
    demand a deletion_records match for non-deletion request types too —
    needs a filtered missing-match (type == "deletion" only).

Encryption Controls (EN-001, EN-003, EN-004):
  - EN-001 "Sensitive data must be encrypted": data_assets.sensitivity
    exists specifically to scope this control to sensitive assets only —
    an unfiltered missing_match would demand encryption_config for every
    asset regardless of sensitivity, which is not what the control asks
    for. Needs a filtered missing-match.
  - EN-003 "TLS certificates must be valid" / "identify certificates
    approaching expiry": needs a relative-date window — the same gap as
    CERT-001, over the same underlying certificates table.
  - EN-004 "Weak encryption protocols must be identified": system_
    configurations and security_baselines share no join_field (system_id
    vs a generic "control" name), and even if joined, comparing tls_
    version to required_value is a field-to-field (and version-string, not
    numeric) comparison, not a literal-value one.

Certificate Management (CERT-001, CERT-002):
  - CERT-001 "Certificates must not expire unexpectedly" / "identify
    certificates expiring within threshold": needs a relative-date window
    (expires_at vs "now" + N days).
  - CERT-002 "Expired certificates must be identified": needs a
    current-time comparison (expires_at < "now") — the value being
    compared against changes every time the rule runs, which no literal
    baked into a template at migration time can represent.

API Controls (API-002, API-003, API-005):
  - API-002 "API access must be authorised": api_access has no employee_id
    field, only user_id — determining "no formal role/permission on file"
    (the seeded violation, a contractor with no HR record) needs a 3-way
    chain (api_access -> user -> employee), which cross_match_condition
    (2 objects only) cannot express.
  - API-003 "Failed API authentication should be monitored" / "identify
    unusual failed requests": api_logs.source_ip repeating IS detectable
    with DuplicateRule, but group_by has no way to first filter to only
    status == "auth_failed" rows — an unfiltered duplicate check on
    (source_ip, status) would also flag ordinary repeated SUCCESSFUL
    logins from the same IP as "unusual failed requests," a wrong result,
    not an incomplete one. Needs a filtered-duplicate capability.
  - API-005 "API keys should expire" / "identify expired/old API keys":
    needs a current-time comparison (expires_at < "now") — the same gap
    as CERT-002.

Vulnerability Management (VM-001, VM-002, VM-003):
  - VM-001 "Vulnerabilities must be identified" / "import vulnerability
    scan results": this audit_procedure describes a data-ingestion
    process, not a row-level comparison — no canonical field distinguishes
    "identified" from "not identified" for a vulnerability that, by
    definition, already exists as a row once it's been scanned. There is
    no concrete condition here for any of the 4 primitives to test.
  - VM-002 "Critical vulnerabilities must be remediated within SLA": needs
    both a relative-date window (discovered_at + sla_rules.
    remediate_within_days vs "now") AND a dynamic per-severity threshold
    lookup — the same per-row dynamic-lookup gap as PR-002/GL-007.
  - VM-003 "Unsupported systems must be identified": needs a relative-date
    comparison (support_matrix.supported_until vs "now") — the same gap
    as AS-005/PM-003, over a related but distinct pair of tables.

Revision ID: 0048
Revises: 0047
Create Date: 2026-09-15
"""
import json

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

_TEMPLATES: list[tuple[str, str, dict]] = [
    (
        "DP-001",
        "Data access grant has no recorded authorisation",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "data_access",
            "secondary_object": "data_access",
            "join_field": "access_id",
            "condition_primary": {"field": "granted_by", "operator": "is_null"},
            "condition_secondary": {"field": "access_id", "operator": "is_not_null"},
        },
    ),
    (
        "DP-003",
        "User has excessive (full) access to personal information",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "user_access",
            "secondary_object": "data_classification",
            "join_field": "dataset",
            "condition_primary": {"field": "access_level", "operator": "eq", "value": "full"},
            "condition_secondary": {"field": "classification", "operator": "eq", "value": "personal_info"},
        },
    ),
    (
        "EN-002",
        "Encryption key is marked unauthorised",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "encryption_keys",
            "secondary_object": "encryption_keys",
            "join_field": "key_id",
            "condition_primary": {"field": "authorized", "operator": "eq", "value": False},
            "condition_secondary": {"field": "key_id", "operator": "is_not_null"},
        },
    ),
    (
        "CERT-003",
        "Certificate is not tied to a known system",
        {"rule_type": "missing_match", "primary_object": "certificates", "secondary_object": "system", "join_field": "system_id"},
    ),
    (
        "CERT-004",
        "Certificate has no renewal record on file",
        {"rule_type": "missing_match", "primary_object": "certificates", "secondary_object": "certificate_renewals", "join_field": "cert_id"},
    ),
    (
        "API-001",
        "API is not registered",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "api_inventory",
            "secondary_object": "api_inventory",
            "join_field": "api_id",
            "condition_primary": {"field": "registered", "operator": "eq", "value": False},
            "condition_secondary": {"field": "api_id", "operator": "is_not_null"},
        },
    ),
    (
        "API-004",
        "API does not use TLS encryption",
        {
            "rule_type": "cross_match_condition",
            "primary_object": "api_configurations",
            "secondary_object": "api_configurations",
            "join_field": "api_id",
            "condition_primary": {"field": "tls_enabled", "operator": "eq", "value": False},
            "condition_secondary": {"field": "api_id", "operator": "is_not_null"},
        },
    ),
    (
        "VM-004",
        "Remediated vulnerability has no retest on file",
        {"rule_type": "missing_match", "primary_object": "remediation_actions", "secondary_object": "retests", "join_field": "vuln_id"},
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
            FROM control_library
            WHERE control_code = '{control_code}'
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
