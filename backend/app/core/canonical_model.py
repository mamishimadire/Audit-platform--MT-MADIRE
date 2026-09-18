"""
The Canonical Audit Data Model (product spec Section 19) — a fixed,
platform-wide taxonomy that every connector's discovered fields map into.
It is NOT a copy of any client's schema, and it is not tenant-editable
data, which is why it lives here as a versioned constant rather than a
database table: schema.sql's `test_data_mappings.canonical_field` is
already free text specifically so this list can grow without a migration.

Matching is a rule-based heuristic — abbreviation expansion + token overlap
+ table-name-based object inference — not machine learning. That is a
deliberate choice per the build instruction's Section 38 ("Do NOT
implement... Advanced AI... unless the core platform is stable"), and it is
good enough to separate "EMP_NO on an EMP_MASTER table is almost certainly
employee.employee_id" from "SUPV_FLAG could be almost anything," which is
the actual job of the confidence score.
"""
import difflib
import re

CANONICAL_MODEL: dict[str, list[str]] = {
    # --- Original 13 objects — DO NOT rename or remove any field already
    # here. app/services/test_rule_service.py's one real, human-verified
    # rule template (AC-002, migration 0027) reads employee.employee_id,
    # employee.employment_status, user.employee_id and user.status by exact
    # name; renaming any of those silently breaks the only control in the
    # 157-control library with an actual executing test today. "name" is
    # added as an extra employee field (not a replacement) so a client's
    # literal "name" column still scores an exact match. "status" is
    # deliberately NOT added here even though it's a very common real
    # column name — a real hr_employees.status field must still lose to
    # employment_status on confidence (a real, if imperfect, ~75-85%
    # fuzzy/boosted match) rather than win outright at 100% on an
    # unrelated exact-name alias, which would make AC-002 auto-suggest the
    # wrong canonical field for the one column its rule actually reads.
    # "status" as its own concept already exists generically wherever a
    # newer table-specific object needs it (e.g. hr_employees's actual
    # data still maps to employee.employment_status via fuzzy match, not
    # a same-named but wrong sibling field).
    "employee": [
        "employee_id",
        "full_name",
        "name",
        "employment_status",
        "termination_date",
        "hire_date",
        "department_id",
        "job_title",
    ],
    "user": ["user_id", "username", "status", "last_login", "employee_id", "roles"],
    "role": ["role_id", "role_name", "description"],
    "privilege": ["privilege_id", "privilege_name"],
    "system": ["system_id", "system_name", "criticality", "owner"],
    "login": ["login_id", "login_timestamp", "username"],
    "transaction": ["transaction_id", "transaction_date", "amount", "account"],
    "change": ["change_id", "change_date", "approved_by"],
    "vendor": ["vendor_id", "vendor_name", "bank_account"],
    "department": ["department_id", "department_name"],
    # CRM objects — added for the HubSpot connector, but engine-agnostic
    # like everything else here: any CRM (Salesforce, Zoho, ...) discovered
    # in the future maps into these same three, no connector-specific model.
    "customer": ["customer_id", "customer_name", "email", "phone", "company_name", "lifecycle_stage"],
    "deal": ["deal_id", "deal_name", "amount", "deal_stage", "close_date", "owner"],
    "company": ["company_id", "company_name", "industry", "domain", "annual_revenue"],
    # --- Everything below is new: one object per required_tables entry
    # from app/core/control_library_data.py (130 unique tables across the
    # 20-domain, 157-control library), keyed by the table's own name so
    # infer_object_for_entity's exact-token-overlap bonus actually fires for
    # it (unlike the legacy objects above, whose singular names never
    # token-match their plural table names — a pre-existing quirk left
    # alone rather than risking the one real template). Field lists are the
    # literal field names backend/scripts/seed_mongo_demo.py writes for
    # that collection, which is the actual ground truth of what this data
    # looks like — an exact-string field match scores 100 automatically
    # (see suggest_canonical_field's token-overlap + sequence-ratio, both
    # of which max out on identical strings), so this is the single highest
    # -leverage way to make every real, seeded field suggest correctly
    # instead of being forced into an unrelated legacy object (the original
    # bug: certificates.common_name mapping to customer.company_name).
    #
    # 1. User Access Management / Segregation of Duties
    # hr_employees/system_users/users/roles are deliberately NOT separate
    # objects here — their fields are identical to the legacy employee/user/
    # role objects above (now enriched to match), and adding exact-duplicate
    # objects would create a same-object-name-vs-different-object 100%-vs-
    # 100% tie the preferred_object bonus can't break in the legacy object's
    # favor for THESE specific tables, since infer_object_for_entity can't
    # match "hr_employees"/"system_users"/"users"/"roles" (plural/prefixed)
    # to "employee"/"user"/"role" (singular) anyway — see that function's
    # docstring. Dict insertion order alone keeps ties resolved toward the
    # legacy objects declared above, which is what AC-002's one real,
    # human-verified rule template (employee.employee_id, employee.
    # employment_status, user.employee_id, user.status) actually needs.
    "user_roles": ["user_id", "role"],
    "role_permissions": ["role", "permission"],
    "sod_rules": ["rule_id", "conflicting_permissions", "description"],
    "access_requests": ["request_id", "user_id", "requested_role", "status", "approved_by", "requested_at"],
    "access_reviews": ["review_id", "user_id", "reviewed_at", "reviewed_by", "outcome"],
    "user_access_changes": ["change_id", "user_id", "change_type", "new_role", "requested_at", "approved"],
    "login_history": ["user_id", "login_at", "ip_address"],
    # 2. Procure-to-Pay / Master Data
    "suppliers": ["supplier_id", "supplier_name", "tax_number", "bank_account", "status"],
    "supplier_approvals": ["supplier_id", "approved_by", "approved_at"],
    "supplier_bank_changes": ["change_id", "supplier_id", "old_account", "new_account", "changed_at", "approved_by"],
    "approvals": ["approval_id", "reference", "approved_by", "approved_at"],
    "approval_limits": ["role", "max_amount"],
    "purchase_orders": ["po_number", "supplier_id", "amount", "created_by", "created_at", "status"],
    "po_approvals": ["po_number", "approved_by", "approved_at"],
    "goods_receipts": ["grn_number", "po_number", "received_quantity", "received_at"],
    # "processed_by" added (round-3 pass) for SOD-003 ("Invoice creation
    # and payment should be segregated"): every OTHER transactional
    # document object this same segregation-of-duties pattern is tested
    # against already carries an actor field (purchase_orders.created_by,
    # journal_entries.prepared_by) — supplier_invoices was the one
    # document type missing it, which is why 0050 found "there is nothing
    # on the primary side to compare against payments.paid_by in the
    # first place." Deliberately NOT extending this same treatment to
    # master-data objects (customers/suppliers/employee, for SOD-005/
    # SOD-006) — those consistently have no creator field anywhere in the
    # model (suppliers itself has none either), so adding one there would
    # be introducing a new pattern rather than completing an existing one;
    # only this transactional-document case is a genuine like-for-like gap.
    "supplier_invoices": ["invoice_number", "supplier_id", "po_number", "amount", "quantity", "received_at", "processed_by"],
    "invoice_approvals": ["invoice_number", "approved_by", "approved_at"],
    "payments": ["payment_id", "invoice_number", "supplier_id", "amount", "paid_by", "paid_at", "status"],
    "payment_approvals": ["payment_id", "approved_by", "approved_at"],
    "customer_approvals": ["customer_id", "approved_by", "approved_at"],
    "credit_approvals": ["customer_id", "credit_limit", "approved_by", "approved_at"],
    # 3. Financial / General Ledger
    "accounting_periods": ["period", "start_date", "end_date", "status"],
    "journal_entries": ["journal_id", "description", "amount", "account", "prepared_by", "posted_at", "period", "entry_type"],
    "journal_approvals": ["journal_id", "approved_by", "approved_at"],
    "journal_lines": ["journal_id", "line", "debit", "credit"],
    # "account_type" added (round-3 pass) for GL-008 ("Suspense accounts
    # require review"): general_ledger previously had no way to identify a
    # suspense account other than free-text account_name, which 0044-0051
    # correctly refused to hardcode as a magic string. A classification
    # field is a completely standard GL attribute in any real chart of
    # accounts (asset/liability/suspense/...), so this is filling an
    # obviously-missing attribute, not inventing structure.
    "general_ledger": ["account", "account_name", "account_type", "balance", "as_of"],
    "ap_transactions": ["transaction_id", "supplier_id", "amount", "posted_at"],
    "ar_transactions": ["transaction_id", "customer_id", "amount", "posted_at"],
    "inventory": ["item_code", "description", "quantity_on_hand", "unit_cost"],
    # 4. Payroll
    "payroll": ["pay_id", "employee_id", "period", "gross_pay", "net_pay"],
    "payroll_history": ["employee_id", "period", "gross_pay"],
    "employee_approvals": ["employee_id", "approved_by", "approved_at"],
    "salary_changes": ["change_id", "employee_id", "old_salary", "new_salary", "changed_at"],
    "salary_approvals": ["change_id", "approved_by", "approved_at"],
    "bank_accounts": ["employee_id", "account_number"],
    "employee_documents": ["employee_id", "document_type", "on_file"],
    "payroll_changes": ["change_id", "employee_id", "field", "changed_at"],
    "audit_logs": ["log_id", "entity_type", "entity_id", "action", "logged_at"],
    # 5. Master Data / Order-to-Cash
    "customers": ["customer_id", "customer_name", "tax_number", "credit_limit", "status"],
    # "discount_amount" added (round-3 pass) for OTC-006 ("Discounts
    # require approval"): discount_approvals already records discount_
    # amount for the approval side, but sales_orders itself had no field
    # to identify WHICH orders carried a discount in the first place —
    # without it there is no way to scope the check to discounted orders
    # only (an unfiltered version would wrongly demand an approval row for
    # every order, discounted or not). Mirrors discount_approvals'
    # existing field name exactly.
    "sales_orders": ["order_id", "customer_id", "amount", "discount_amount", "created_at"],
    "sales_approvals": ["order_id", "approved_by", "approved_at"],
    "sales_invoices": ["invoice_id", "order_id", "customer_id", "amount", "issued_at"],
    "deliveries": ["delivery_id", "invoice_id", "delivered_at"],
    "discount_approvals": ["order_id", "approved_by", "approved_at", "discount_amount"],
    "customer_receipts": ["receipt_id", "customer_id", "amount", "invoice_id", "received_at"],
    "receivables": ["customer_id", "outstanding_amount", "days_overdue"],
    # 6. Change Management
    "change_requests": ["change_id", "description", "requested_by", "requested_at", "scheduled_window"],
    "change_approvals": ["change_id", "approved_by", "approved_at"],
    "production_changes": ["change_id", "deployed_at", "deployed_by"],
    "test_results": ["change_id", "result", "tested_at"],
    "code_changes": ["change_id", "committed_by"],
    "deployments": ["change_id", "deployed_by", "deployed_at"],
    "production_logs": ["log_id", "change_id", "logged_at"],
    # 7. IT Operations
    # "max_duration_seconds" added (round-3 pass) for OP-004 ("Processing
    # time should remain within limits"): job_executions.duration_seconds
    # was already real and usable, but no field anywhere defined what
    # limit a given job should be held to — 0044/0049/0051 correctly
    # refused to invent a hardcoded cutoff. A per-job expected-duration
    # ceiling is a standard scheduler attribute (most job schedulers let
    # you configure exactly this), so this fills an obviously-missing
    # attribute on the job definition itself rather than fabricating a
    # policy value.
    "scheduled_jobs": ["job_id", "job_name", "schedule", "critical", "max_duration_seconds"],
    "job_executions": ["job_id", "executed_at", "status", "duration_seconds"],
    "incident_records": ["incident_id", "job_id", "opened_at"],
    "incidents": ["incident_id", "description", "severity", "opened_at", "status"],
    "system_logs": ["log_id", "system", "logged_at"],
    "log_retention_rules": ["system", "retention_days"],
    # 8. Backup & Recovery
    # "systems" (the required table) is deliberately not its own object —
    # identical fields to the legacy "system" object above, same
    # exact-duplicate tie-break reasoning as section 1.
    "backup_jobs": ["backup_id", "system_id", "run_at", "status"],
    "backup_schedules": ["system_id", "frequency"],
    "backup_incidents": ["backup_id", "system_id", "logged_at"],
    "backup_retention_rules": ["system_id", "retention_days"],
    "recovery_tests": ["system_id", "tested_at", "result"],
    # 9. IT Asset Management
    "asset_register": ["asset_id", "asset_name", "assigned_to", "status"],
    "system_inventory": ["system_id", "os", "version"],
    "software_inventory": ["asset_id", "software_name", "version"],
    "approved_software": ["software_name", "risk_level"],
    "support_matrix": ["os", "supported_until"],
    # 10. Data Privacy
    "data_access": ["access_id", "user_id", "data_classification", "granted_by", "granted_at"],
    "data_classification": ["dataset", "classification"],
    "user_access": ["user_id", "dataset", "access_level"],
    # "dataset" added (round-4 pass, migration 0060) for DP-004 ("Data
    # retention requirements must be followed"): retention_rules is keyed
    # per-dataset (e.g. "applicant_records" -> 730 days), but data_records
    # itself carried no field identifying WHICH dataset a given record
    # belongs to — without it there is no way to join a record to its own
    # retention rule at all, only to guess a single global day-count for
    # every record regardless of type. Every other object in this model
    # that a retention/classification rule keys against already carries the
    # matching attribute (data_classification.dataset, user_access.dataset)
    # — data_records was the one record-level object missing its own copy,
    # the same "obviously missing attribute on the object the control's own
    # required_tables names first" pattern that justified account_type
    # (GL-008), max_duration_seconds (OP-004), and exception_id
    # (patch_exceptions) in the prior round.
    "data_records": ["record_id", "subject", "created_at", "dataset"],
    "retention_rules": ["dataset", "retention_days"],
    "privacy_requests": ["request_id", "type", "requested_at"],
    "deletion_records": ["request_id", "deleted_at"],
    # 11. Encryption Controls
    "data_assets": ["asset_name", "sensitivity"],
    "encryption_config": ["asset_name", "encrypted", "algorithm"],
    "encryption_keys": ["key_id", "purpose", "expires_at", "authorized"],
    "key_management_logs": ["key_id", "action", "logged_at"],
    "system_configurations": ["system_id", "tls_version"],
    "security_baselines": ["control", "required_value"],
    # 12. Certificate Management
    "certificates": ["cert_id", "system_id", "common_name", "expires_at", "issued_to_system"],
    "certificate_renewals": ["cert_id", "renewed_at", "renewed_by"],
    # 13. API Controls
    "api_inventory": ["api_id", "api_name", "system_id", "registered"],
    "api_access": ["api_id", "user_id", "granted_at"],
    "api_logs": ["api_id", "status", "occurred_at", "source_ip"],
    "api_configurations": ["api_id", "tls_enabled"],
    "api_keys": ["key_id", "api_id", "created_at", "expires_at"],
    # 14. Vulnerability Management
    "assets": ["asset_id", "hostname"],
    "vulnerabilities": ["vuln_id", "asset_id", "severity", "discovered_at", "status"],
    "remediation_actions": ["vuln_id", "action", "completed_at"],
    "sla_rules": ["severity", "remediate_within_days", "patch_within_days"],
    "retests": ["vuln_id", "retested_at", "result"],
    # 15. Network Controls
    "network_connections": ["connection_id", "source", "destination", "approved"],
    "approved_connections": ["connection_id"],
    "firewall_rules": ["rule_id", "source", "destination", "port", "action"],
    "firewall_approvals": ["rule_id", "approved_by", "approved_at"],
    "network_devices": ["device_id", "hostname"],
    # 16. Web Filtering / Content Security
    "web_filter_status": ["asset_id", "filtering_active", "on_network"],
    "web_filter_policies": ["policy_id", "category", "blocked"],
    "web_filter_logs": ["user_id", "url", "action", "occurred_at"],
    "security_logs": ["log_id", "event", "logged_at"],
    "web_filter_exceptions": ["exception_id", "user_id", "reason", "granted_at"],
    "exception_approvals": ["exception_id", "approved_by", "approved_at"],
    "web_filter_config": ["config_id", "category_db_version", "updated_at"],
    # 17. Patch Management
    "patch_inventory": ["asset_id", "patch_id", "installed", "installed_at"],
    "patch_releases": ["patch_id", "severity", "released_at"],
    "patch_catalogue": ["patch_id", "applies_to_os"],
    "patch_deployments": ["deployment_id", "patch_id", "asset_id", "deployed_at", "approved", "tested", "emergency"],
    "patch_approvals": ["deployment_id", "approved_by", "approved_at"],
    "patch_incidents": ["deployment_id", "issue", "resolved"],
    # "exception_id" added (round-3 pass) for PM-010 ("Patch exceptions/
    # deferrals require documented justification"): every other exception-
    # tracking object in the library that needs an approval trail has its
    # own per-row id (web_filter_exceptions.exception_id, paired with
    # exception_approvals.exception_id) — patch_exceptions was the one
    # exception-style object missing it, which is why 0049 found "the
    # generic exception_approvals table... can't be reused here despite
    # the very similar-sounding name." Adding the same id field this
    # object's own sibling already has is completing an established
    # pattern, not inventing a new one.
    "patch_exceptions": ["exception_id", "asset_id", "reason"],
    # 18. Remote Access
    # "outcome" added (round-3 pass) for RA-011 ("Failed remote login
    # attempts must be monitored"): 0049 found this was "a genuine missing-
    # canonical-field gap" — neither remote_access_logs nor login_history
    # had any success/failure field at all. A login/session outcome is
    # about as basic an attribute as an access-log object can have (nearly
    # every real auth/VPN log carries one), so this fills an obviously-
    # missing attribute on the object this control's own required_tables
    # names first.
    "remote_access_logs": ["user_id", "session_id", "started_at", "mfa_used", "source_country", "idle_minutes", "outcome"],
    "mfa_config": ["user_id", "mfa_enabled"],
    "remote_access_grants": ["user_id", "granted_at", "approved"],
    "vpn_accounts": ["user_id", "vpn_username", "status"],
    "session_audit": ["session_id", "logged"],
    "access_policies": ["policy_id", "allowed_countries", "allowed_hours"],
    "privileged_access_approvals": ["session_id", "approved_by", "approved_at"],
    "vendor_access_grants": ["vendor", "granted_at", "expires_at"],
    "remote_access_tools": ["tool_name", "in_use_on"],
    "session_policies": ["policy_id", "max_idle_minutes"],
    "vpn_config": ["config_id", "split_tunnel_enabled"],
    "network_policies": ["policy_id", "split_tunnel_allowed"],
}

AUTO_ACCEPT_THRESHOLD = 90.0

# Common abbreviations seen in real client schemas (EMP_MASTER, TERM_DT, ...) —
# expanding these before comparing tokens is what separates a genuinely close
# match from an unrelated one.
_ABBREVIATIONS = {
    "emp": "employee",
    "no": "number",
    "num": "number",
    "nbr": "number",
    "stat": "status",
    "sts": "status",
    "dt": "date",
    "cd": "code",
    "dept": "department",
    "desc": "description",
    "addr": "address",
    "qty": "quantity",
    "amt": "amount",
    "txn": "transaction",
    "acct": "account",
    "supv": "supervisor",
    "mgr": "manager",
    "usr": "user",
    "pwd": "password",
    "ts": "timestamp",
    "master": "",
    "tbl": "",
    "flg": "flag",
}

_ID_LIKE_TOKENS = {"number", "id", "code"}

# MongoDB's synthetic per-document key. It genuinely IS a collection's
# primary key (the discovery connector correctly reports is_primary_key for
# it), but it is a technical row identifier, never the client's own
# business identifier — a real employee_id/user_id/cert_id column always
# exists as its own separate field wherever one matters (see every
# collection in scripts/seed_mongo_demo.py). Boosting it toward
# "employee.employee_id" or "system.system_id" just because it ends in
# "_id" and is flagged as a key produced exactly the false positives an
# auditor caught by hand: _id confidently "matching" a business key it has
# no real relationship to, once even accepted alongside the column that
# actually is that key. No real RDBMS column is ever named this either
# (leading underscore), so excluding it by exact name is safe.
_SYNTHETIC_ID_FIELD_NAMES = {"_id"}


def _tokens(name: str) -> list[str]:
    raw = re.split(r"[^a-zA-Z0-9]+", name)
    expanded = [_ABBREVIATIONS.get(t.lower(), t.lower()) for t in raw if t]
    return [t for t in expanded if t]


def _token_overlap_score(source_tokens: set[str], target_tokens: set[str]) -> float:
    if not source_tokens or not target_tokens:
        return 0.0
    union = source_tokens | target_tokens
    overlap = source_tokens & target_tokens
    return len(overlap) / len(union) * 100


# Table names whose preferred object can't be found by plain token overlap
# with a CANONICAL_MODEL key — either because the real canonical object is
# deliberately NOT keyed under this exact table name (system_users/users/
# roles were kept out of the dict entirely to avoid the exact-duplicate-
# field tie-break problem documented above the "1. User Access Management"
# section), or because a *different* key happens to share one token by
# coincidence and would otherwise win instead (system_users tokenizes to
# {"system", "users"}, which partially matches the unrelated "system"
# object — wrongly biasing every system_users field toward "system.*"
# candidates instead of "user.*"). Checked before the token-overlap scan.
_ENTITY_NAME_ALIASES: dict[str, str] = {
    "hr_employees": "employee",
    "employees": "employee",
    "system_users": "user",
    "users": "user",
    "roles": "role",
    "systems": "system",
}


def infer_object_for_entity(entity_name: str) -> str | None:
    """Table names carry real signal — EMP_MASTER strongly implies the 'employee' object."""
    alias = _ENTITY_NAME_ALIASES.get(entity_name.strip().lower())
    if alias is not None:
        return alias
    entity_tokens = set(_tokens(entity_name))
    if not entity_tokens:
        return None
    best_object, best_score = None, 0.0
    for obj_name in CANONICAL_MODEL:
        obj_tokens = set(_tokens(obj_name))
        overlap = entity_tokens & obj_tokens
        if not overlap:
            continue
        # Jaccard ratio, not raw shared-token count — a raw count lets a
        # longer superset tie an EXACT match on the same shared tokens
        # (e.g. "user_access" vs "user_access_changes" both share {"user",
        # "access"}), and the first one Python happens to iterate then
        # wins the tie regardless of which is actually the real match.
        # Dividing by the union makes an exact match score 1.0 and always
        # win outright over any partial/superset one, which is what "this
        # table's name IS a canonical object's name" should always mean.
        score = len(overlap) / len(entity_tokens | obj_tokens)
        if score > best_score:
            best_score = score
            best_object = obj_name
    return best_object


# Below this, a same-object match is too weak to trust over a plain
# global search — e.g. a genuinely unrelated field name shouldn't get
# force-mapped into the preferred object just because it's *technically*
# the least-bad candidate there. Chosen well below AUTO_ACCEPT_THRESHOLD:
# this only decides which SEARCH SPACE wins, not whether a match is good
# enough to auto-accept (mapping_status_for_confidence still applies to
# the final score either way).
_PREFERRED_OBJECT_MIN_SCORE = 35.0

# Caps a global-search result when preferred_object is known but none of
# ITS OWN fields cleared _PREFERRED_OBJECT_MIN_SCORE. This is the case that
# actually matters most: we have a real signal for what this column's table
# represents (e.g. a "payments" table), and that object genuinely has no
# field shaped like this one (e.g. "status" — payments has no status-like
# concept in CANONICAL_MODEL). Without a cap, the unconstrained search below
# can still find an EXACT name match on a totally unrelated object (e.g.
# "user.status") and return it at 100 — confident enough to auto-accept
# with no human ever reviewing it. That is precisely backwards: an exact
# name hit on the WRONG object, for a table we already know is something
# else, is a coincidence, not a correspondence. Set below the weakest
# possible in-object result (_PREFERRED_OBJECT_MIN_SCORE + 25 = 60) so a
# cross-object fallback guess never outranks even a mediocre same-object
# one, and always sits under AUTO_ACCEPT_THRESHOLD.
_CROSS_OBJECT_FALLBACK_CAP = 55.0

# Below this, the "best available" match isn't a real candidate at all —
# just the least-bad of a field of coincidences (e.g. a users table's
# "gender" or "password_hash" column, which have no corresponding concept
# anywhere in CANONICAL_MODEL because no control needs one — CANONICAL_MODEL
# only ever models what the 157 controls actually read, never a full source
# schema). Only the unconstrained global-search branch can ever score this
# low — the in-object branch's own floor (_PREFERRED_OBJECT_MIN_SCORE + the
# fixed +25 boost) never produces anything below 60. Forcing a specific-
# looking "56% match: some_object.some_field" onto a column that has no real
# canonical counterpart is worse than admitting there isn't one — it invites
# an auditor to trust a number that was never measuring a real
# correspondence, exactly the failure mode reported from a real client
# schema's users table (password_hash, gender, is_platform_admin, etc. all
# forced onto unrelated fields in the 40s%). suggest_canonical_field returns
# ("", 0.0) below this instead, and callers show "no confident match."
_NO_MATCH_FLOOR = 50.0

# _score_field's SequenceMatcher ratio operates on sorted-token-joined
# strings, which is character-level, not word-level — for SHORT field
# names this makes it easy for two genuinely unrelated words to share
# enough letters by coincidence to look like a real match ("method" vs
# "payment_id" share m/e/t/d, scoring ~40% before any boost; "company_id"
# vs "paid_by" similarly). That coincidence is only trustworthy when it's
# high enough to mean "the same word(s) with the separator removed"
# (e.g. "employeenumber" vs "employee_number" tokenizes as one blob vs
# two words, zero token overlap, but scores ~100% here) — not merely
# "vaguely similar-looking letters." Below this, a zero-token-overlap
# seq_ratio is discarded rather than trusted as any part of the score.
_NO_OVERLAP_SEQ_RATIO_FLOOR = 75.0


def suggest_canonical_field(
    source_field_name: str, *, is_primary_key: bool = False, preferred_object: str | None = None
) -> tuple[str, float]:
    """Returns (best "object.field" match, confidence 0-100).

    When preferred_object is known (the table this field came from strongly
    implies one canonical object — see infer_object_for_entity), a
    plausible match *within that object* wins outright over a numerically
    higher-scoring match on a completely unrelated object. This matters
    because CANONICAL_MODEL is large enough now (130+ objects covering the
    full control library) that generic column names like "status" or
    "created_at" are exact matches on dozens of unrelated objects — without
    this, "this table is clearly about employees" loses to "some unrelated
    object also happens to have a field called status," which is exactly
    backwards. A flat scoring bonus can't fix this on its own: it needs to
    beat every possible exact match anywhere else in the model, not just
    nudge past one particular competitor.
    """
    source_tokens = set(_tokens(source_field_name))
    normalized_source = "".join(sorted(source_tokens))
    # See _SYNTHETIC_ID_FIELD_NAMES — a technical row key never gets the
    # primary-key business-identifier boost, no matter what the connector
    # reported for is_primary_key.
    treat_as_primary_key = is_primary_key and source_field_name not in _SYNTHETIC_ID_FIELD_NAMES

    def _score_field(field_name: str, obj_name: str) -> float:
        target_tokens = set(_tokens(field_name))
        overlap_score = _token_overlap_score(source_tokens, target_tokens)
        normalized_target = "".join(sorted(target_tokens))
        seq_ratio = difflib.SequenceMatcher(None, normalized_source, normalized_target).ratio() * 100
        if source_tokens & target_tokens:
            score = max(overlap_score, seq_ratio)
        elif seq_ratio >= _NO_OVERLAP_SEQ_RATIO_FLOOR:
            score = seq_ratio
        else:
            score = 0.0
        # A primary-key column with an id-like token (NO/CODE/NUM) mapping to
        # this object's own "_id" field is the single strongest real-world signal.
        if treat_as_primary_key and field_name.endswith("_id") and (source_tokens & _ID_LIKE_TOKENS):
            candidate = 96.0 if obj_name == preferred_object else 75.0
            score = max(score, candidate)
        return score

    if preferred_object is not None and preferred_object in CANONICAL_MODEL:
        best_in_object, best_in_object_score = "", 0.0
        for field_name in CANONICAL_MODEL[preferred_object]:
            score = _score_field(field_name, preferred_object)
            if score > best_in_object_score:
                best_in_object_score, best_in_object = score, field_name
        if best_in_object_score >= _PREFERRED_OBJECT_MIN_SCORE:
            # A visible, if modest, confidence boost for having the right
            # table-name signal — capped at 100 since there's no cross-object
            # tie to out-score within this branch, only other fields on the
            # SAME object, which the loop above already picked the best of.
            return f"{preferred_object}.{best_in_object}", round(min(best_in_object_score + 25, 100.0), 2)

    best_field, best_score = "", 0.0
    for obj_name, fields in CANONICAL_MODEL.items():
        for field_name in fields:
            score = _score_field(field_name, obj_name)
            if score > best_score:
                best_score, best_field = score, f"{obj_name}.{field_name}"

    if (
        preferred_object is not None
        and best_field
        and not best_field.startswith(f"{preferred_object}.")
    ):
        best_score = min(best_score, _CROSS_OBJECT_FALLBACK_CAP)

    if best_score < _NO_MATCH_FLOOR:
        return "", 0.0

    return best_field, round(best_score, 2)


def mapping_status_for_confidence(confidence: float) -> str:
    return "auto" if confidence >= AUTO_ACCEPT_THRESHOLD else "needs_review"


# Below this, a name match is noise, not a real candidate — not worth
# showing an auditor "system_users ~ 14% match: invoice_approvals".
TABLE_SUGGESTION_MIN_SCORE = 40.0


def score_table_name_match(canonical_table_name: str, candidate_entity_name: str) -> float:
    """How likely `candidate_entity_name` (a discovered table/collection) is
    to be the physical table a control's required canonical table name
    (e.g. "system_users") refers to. Reuses the same token-overlap +
    fuzzy-ratio approach as suggest_canonical_field, just compared directly
    name-to-name instead of against a fixed canonical field list — control
    required_tables are already free-form strings (schema.sql's
    canonical_field is deliberately not an enum either, see the module
    docstring), not a second taxonomy to maintain here.
    """
    source_tokens = set(_tokens(canonical_table_name))
    target_tokens = set(_tokens(candidate_entity_name))
    token_score = _token_overlap_score(source_tokens, target_tokens)

    normalized_source = "".join(sorted(source_tokens))
    normalized_target = "".join(sorted(target_tokens))
    seq_ratio = difflib.SequenceMatcher(None, normalized_source, normalized_target).ratio() * 100

    # A literal (case-insensitive) match — the common case when a
    # connector's table/collection name already matches a canonical name
    # verbatim — always wins outright over any heuristic score.
    if canonical_table_name.strip().lower() == candidate_entity_name.strip().lower():
        return 100.0

    return round(max(token_score, seq_ratio), 2)
