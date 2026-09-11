"""
Seeds a MongoDB database with demo collections covering every table the
platform's control library (backend/app/core/control_library_data.py)
references — 130 unique tables across 157 controls / 20 domains. Purpose:
let a real end-to-end test happen (connect -> discover -> map -> approve ->
activate -> execute -> exception -> finding) against real data instead of
an empty database.

Every collection mixes compliant records with a handful of DELIBERATE
violations matching that domain's audit procedures (an unapproved journal,
a terminated employee still active, an expired certificate, a PO over its
approval limit, ...) so running the actual audit tests against this data
produces real exceptions to verify against — not just "0 findings" because
the data was too clean to fail anything.

Idempotent: drops the target database first, so re-running gives a clean,
predictable dataset rather than accumulating duplicates.

Usage:
    MONGODB_URI='mongodb+srv://user:pass@cluster.mongodb.net/' \
    MONGODB_DEMO_DATABASE=mt_audit_demo \
    python -m scripts.seed_mongo_demo
"""
import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient  # noqa: E402

NOW = datetime.now(timezone.utc)


def days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


def days_from_now(n: int) -> datetime:
    return NOW + timedelta(days=n)


# --- Shared reference pools, reused across tables for realistic linkage ---

EMPLOYEES = [
    {"employee_id": "EMP001", "name": "Thandiwe Nkosi", "department": "Finance", "hire_date": days_ago(900), "termination_date": None, "status": "active"},
    {"employee_id": "EMP002", "name": "Johan van der Merwe", "department": "IT", "hire_date": days_ago(1200), "termination_date": None, "status": "active"},
    {"employee_id": "EMP003", "name": "Priya Govender", "department": "Procurement", "hire_date": days_ago(600), "termination_date": None, "status": "active"},
    {"employee_id": "EMP004", "name": "Sipho Dlamini", "department": "Finance", "hire_date": days_ago(1500), "termination_date": days_ago(45), "status": "terminated"},
    {"employee_id": "EMP005", "name": "Anna Botha", "department": "HR", "hire_date": days_ago(800), "termination_date": None, "status": "active"},
    {"employee_id": "EMP006", "name": "Kagiso Molefe", "department": "Sales", "hire_date": days_ago(500), "termination_date": None, "status": "active"},
    {"employee_id": "EMP007", "name": "Lerato Mokoena", "department": "IT", "hire_date": days_ago(700), "termination_date": days_ago(10), "status": "terminated"},
    {"employee_id": "EMP008", "name": "David Pretorius", "department": "Procurement", "hire_date": days_ago(400), "termination_date": None, "status": "active"},
    {"employee_id": "EMP009", "name": "Naledi Khumalo", "department": "Finance", "hire_date": days_ago(1000), "termination_date": None, "status": "active"},
    {"employee_id": "EMP010", "name": "Willem de Klerk", "department": "Sales", "hire_date": days_ago(300), "termination_date": None, "status": "active"},
    {"employee_id": "EMP011", "name": "Zanele Cele", "department": "IT", "hire_date": days_ago(2000), "termination_date": None, "status": "active"},
    {"employee_id": "EMP012", "name": "Peter Nel", "department": "Finance", "hire_date": days_ago(1100), "termination_date": None, "status": "active"},
]

SUPPLIERS = [
    {"supplier_id": "SUP001", "supplier_name": "Highveld Office Supplies", "tax_number": "TX10001", "bank_account": "628001", "status": "active"},
    {"supplier_id": "SUP002", "supplier_name": "Rand IT Distribution", "tax_number": "TX10002", "bank_account": "628002", "status": "active"},
    {"supplier_id": "SUP003", "supplier_name": "Coastal Logistics (Pty) Ltd", "tax_number": "TX10003", "bank_account": "628003", "status": "active"},
    {"supplier_id": "SUP004", "supplier_name": "Golden Gate Consulting", "tax_number": "TX10004", "bank_account": "628004", "status": "inactive"},
    {"supplier_id": "SUP005", "supplier_name": "Highveld Office Supplies", "tax_number": "TX10001", "bank_account": "628001", "status": "active"},  # deliberate duplicate (MD-002)
    {"supplier_id": "SUP006", "supplier_name": "Bushveld Facilities Group", "tax_number": "TX10006", "bank_account": "628006", "status": "active"},
]

CUSTOMERS = [
    {"customer_id": "CUS001", "customer_name": "Metro Retail Group", "tax_number": "CX20001", "credit_limit": 500000, "status": "active"},
    {"customer_id": "CUS002", "customer_name": "Karoo Mining Holdings", "tax_number": "CX20002", "credit_limit": 1200000, "status": "active"},
    {"customer_id": "CUS003", "customer_name": "Cape Coastal Foods", "tax_number": "CX20003", "credit_limit": 250000, "status": "active"},
    {"customer_id": "CUS004", "customer_name": "Lowveld Agri Co-op", "tax_number": "CX20004", "credit_limit": 300000, "status": "inactive"},
]

client: MongoClient | None = None


def seed(collection_name: str, docs: list[dict]) -> None:
    # A deliberately empty list represents "no such record exists" (several
    # violations below rely on exactly that absence, e.g. "no approval was
    # ever logged for this exception"). insert_many([]) itself would raise —
    # but leaving the collection genuinely absent would be worse: MongoDB
    # discovery only lists collections that exist, so an audit test's
    # required table could never be mapped/activated at all. Explicitly
    # create it empty instead, so it's discoverable and the comparison it's
    # meant to fail (X with no matching Y) actually has a Y to compare against.
    if not docs:
        if collection_name not in db.list_collection_names():
            db.create_collection(collection_name)
        print(f"  {collection_name}: 0 documents (created empty, by design)")
        return
    db[collection_name].insert_many(docs)
    print(f"  {collection_name}: {len(docs)} documents")


def main() -> None:
    global client, db
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        print("MONGODB_URI environment variable is required.", file=sys.stderr)
        sys.exit(1)
    database_name = os.environ.get("MONGODB_DEMO_DATABASE", "mt_audit_demo")

    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    client.admin.command("ping")
    client.drop_database(database_name)
    db_local = client[database_name]
    global db
    db = db_local
    print(f"Seeding database '{database_name}'...")

    seed_user_access_and_sod()
    seed_procure_to_pay()
    seed_financial_gl()
    seed_payroll()
    seed_master_data()
    seed_order_to_cash()
    seed_change_management()
    seed_it_operations()
    seed_backup_recovery()
    seed_it_asset_management()
    seed_data_privacy()
    seed_encryption()
    seed_certificate_management()
    seed_api_controls()
    seed_vulnerability_management()
    seed_network_controls()
    seed_web_filtering()
    seed_patch_management()
    seed_remote_access()

    print(f"\nDone. {len(db.list_collection_names())} collections in '{database_name}'.")
    client.close()


# --- User Access Management (AC-*) + Segregation of Duties (SOD-*) ---

def seed_user_access_and_sod() -> None:
    print("User Access Management / Segregation of Duties:")
    seed("hr_employees", EMPLOYEES)

    system_users = [
        {"user_id": "U001", "username": "t.nkosi", "employee_id": "EMP001", "status": "active", "last_login": days_ago(2), "roles": ["ap_clerk"]},
        {"user_id": "U002", "username": "j.vandermerwe", "employee_id": "EMP002", "status": "active", "last_login": days_ago(1), "roles": ["it_admin"]},
        {"user_id": "U003", "username": "p.govender", "employee_id": "EMP003", "status": "active", "last_login": days_ago(3), "roles": ["procurement_officer"]},
        # AC-002/AC-010 violation: terminated employee, account still active
        {"user_id": "U004", "username": "s.dlamini", "employee_id": "EMP004", "status": "active", "last_login": days_ago(50), "roles": ["ap_clerk"]},
        {"user_id": "U005", "username": "a.botha", "employee_id": "EMP005", "status": "active", "last_login": days_ago(5), "roles": ["hr_admin"]},
        # AC-003 violation: dormant, no login in 180+ days, still active
        {"user_id": "U006", "username": "k.molefe", "employee_id": "EMP006", "status": "active", "last_login": days_ago(210), "roles": ["sales_rep"]},
        # AC-002 violation: terminated employee, active account
        {"user_id": "U007", "username": "l.mokoena", "employee_id": "EMP007", "status": "active", "last_login": days_ago(12), "roles": ["it_admin"]},
        {"user_id": "U008", "username": "d.pretorius", "employee_id": "EMP008", "status": "active", "last_login": days_ago(1), "roles": ["procurement_officer"]},
        {"user_id": "U009", "username": "n.khumalo", "employee_id": "EMP009", "status": "active", "last_login": days_ago(4), "roles": ["ap_manager"]},
        {"user_id": "U010", "username": "w.deklerk", "employee_id": "EMP010", "status": "active", "last_login": days_ago(2), "roles": ["sales_rep"]},
        {"user_id": "U011", "username": "z.cele", "employee_id": "EMP011", "status": "active", "last_login": days_ago(1), "roles": ["it_admin", "system_administrator"]},
        # AC-004 violation: no matching employee record
        {"user_id": "U012", "username": "contractor.temp", "employee_id": None, "status": "active", "last_login": days_ago(6), "roles": ["ap_clerk"]},
        # AC-009 violation: generic account
        {"user_id": "U013", "username": "admin", "employee_id": None, "status": "active", "last_login": days_ago(1), "roles": ["system_administrator"]},
        # AC-008 violation: shared account (used by two people, logged separately below in login_history)
        {"user_id": "U014", "username": "shared.finance", "employee_id": None, "status": "active", "last_login": days_ago(1), "roles": ["ap_clerk"]},
        {"user_id": "U015", "username": "p.nel", "employee_id": "EMP012", "status": "active", "last_login": days_ago(2), "roles": ["ap_manager"]},
    ]
    seed("system_users", system_users)
    seed("users", [{"user_id": u["user_id"], "username": u["username"], "status": u["status"]} for u in system_users])

    seed("roles", [
        {"role_id": "R01", "role_name": "ap_clerk", "description": "Creates supplier invoices"},
        {"role_id": "R02", "role_name": "ap_manager", "description": "Approves supplier payments"},
        {"role_id": "R03", "role_name": "it_admin", "description": "System administration"},
        {"role_id": "R04", "role_name": "procurement_officer", "description": "Creates purchase orders"},
        {"role_id": "R05", "role_name": "hr_admin", "description": "HR administration"},
        {"role_id": "R06", "role_name": "sales_rep", "description": "Creates sales orders"},
        {"role_id": "R07", "role_name": "system_administrator", "description": "Full system access — privileged"},
    ])
    seed("user_roles", [
        {"user_id": u["user_id"], "role": r} for u in system_users for r in u["roles"]
    ])
    # SOD-001 violation: U009 (ap_manager) also holds supplier-creation rights
    seed("role_permissions", [
        {"role": "ap_clerk", "permission": "create_supplier_invoice"},
        {"role": "ap_manager", "permission": "approve_payment"},
        {"role": "ap_manager", "permission": "create_supplier"},  # SOD-001 conflict
        {"role": "procurement_officer", "permission": "create_purchase_order"},
        {"role": "system_administrator", "permission": "full_access"},
    ])
    seed("sod_rules", [
        {"rule_id": "SOD-R01", "conflicting_permissions": ["create_supplier", "approve_payment"], "description": "Supplier creation and payment approval must be segregated"},
        {"rule_id": "SOD-R02", "conflicting_permissions": ["create_purchase_order", "approve_purchase_order"], "description": "PO creation and approval must be segregated"},
    ])

    seed("access_requests", [
        {"request_id": "ARQ001", "user_id": "U001", "requested_role": "ap_clerk", "status": "approved", "approved_by": "EMP009", "requested_at": days_ago(880)},
        {"request_id": "ARQ002", "user_id": "U011", "requested_role": "system_administrator", "status": "approved", "approved_by": "EMP002", "requested_at": days_ago(1900)},
        # AC-001 violation: active user with no approved access request on file
        {"request_id": "ARQ003", "user_id": "U006", "requested_role": "sales_rep", "status": "pending", "approved_by": None, "requested_at": days_ago(495)},
    ])
    seed("access_reviews", [
        {"review_id": "REV001", "user_id": "U001", "reviewed_at": days_ago(85), "reviewed_by": "EMP009", "outcome": "confirmed"},
        {"review_id": "REV002", "user_id": "U002", "reviewed_at": days_ago(400), "reviewed_by": "EMP002", "outcome": "confirmed"},  # AC-006 violation: stale review
    ])
    seed("user_access_changes", [
        {"change_id": "UAC001", "user_id": "U005", "change_type": "role_added", "new_role": "hr_admin", "requested_at": days_ago(800), "approved": True},
        {"change_id": "UAC002", "user_id": "U011", "change_type": "role_added", "new_role": "system_administrator", "requested_at": days_ago(30), "approved": False},  # AC-007 violation
    ])
    seed("login_history", [
        {"user_id": "U006", "login_at": days_ago(210), "ip_address": "10.0.4.12"},
        {"user_id": "U014", "login_at": days_ago(1), "ip_address": "10.0.2.50"},
        {"user_id": "U014", "login_at": days_ago(1), "ip_address": "10.0.9.201"},  # AC-008: same account, two very different sources same day
        {"user_id": "U004", "login_at": days_ago(50), "ip_address": "10.0.1.9"},
    ])

    # SOD-001 needs suppliers + payments too — seeded in their own domains below,
    # reusing the same supplier_id/employee_id pools.


def seed_procure_to_pay() -> None:
    print("Procure-to-Pay:")
    seed("suppliers", SUPPLIERS)
    seed("supplier_approvals", [
        {"supplier_id": "SUP001", "approved_by": "EMP009", "approved_at": days_ago(700)},
        {"supplier_id": "SUP002", "approved_by": "EMP009", "approved_at": days_ago(650)},
        {"supplier_id": "SUP003", "approved_by": "EMP009", "approved_at": days_ago(500)},
        # SUP005 (duplicate) and SUP006 have no approval on file — MD-001 violation
    ])
    seed("supplier_bank_changes", [
        {"change_id": "SBC001", "supplier_id": "SUP001", "old_account": "627999", "new_account": "628001", "changed_at": days_ago(200), "approved_by": "EMP009"},
        {"change_id": "SBC002", "supplier_id": "SUP002", "old_account": "627998", "new_account": "628002", "changed_at": days_ago(15), "approved_by": None},  # MD-003 violation
    ])
    seed("approvals", [{"approval_id": "APR001", "reference": "SBC001", "approved_by": "EMP009", "approved_at": days_ago(200)}])

    seed("approval_limits", [
        {"role": "procurement_officer", "max_amount": 50000},
        {"role": "ap_manager", "max_amount": 200000},
    ])
    purchase_orders = [
        {"po_number": "PO1001", "supplier_id": "SUP001", "amount": 12000, "created_by": "EMP003", "created_at": days_ago(60), "status": "approved"},
        {"po_number": "PO1002", "supplier_id": "SUP002", "amount": 45000, "created_by": "EMP008", "created_at": days_ago(55), "status": "approved"},
        {"po_number": "PO1003", "supplier_id": "SUP003", "amount": 180000, "created_by": "EMP003", "created_at": days_ago(40), "status": "approved"},  # PR-002 violation: over procurement_officer's limit
        {"po_number": "PO1004", "supplier_id": "SUP001", "amount": 8000, "created_by": "EMP008", "created_at": days_ago(20), "status": "pending"},  # PR-001 violation: no approval
        {"po_number": "PO1001", "supplier_id": "SUP001", "amount": 12000, "created_by": "EMP003", "created_at": days_ago(60), "status": "approved"},  # PR-005 violation: duplicate PO number
        {"po_number": "PO1005", "supplier_id": "SUP004", "amount": 5000, "created_by": "EMP003", "created_at": days_ago(10), "status": "approved"},  # PR-003 violation: supplier is inactive
    ]
    seed("purchase_orders", purchase_orders)
    seed("po_approvals", [
        {"po_number": "PO1001", "approved_by": "EMP009", "approved_at": days_ago(59)},
        {"po_number": "PO1002", "approved_by": "EMP009", "approved_at": days_ago(54)},
        {"po_number": "PO1003", "approved_by": "EMP009", "approved_at": days_ago(39)},
        {"po_number": "PO1005", "approved_by": "EMP009", "approved_at": days_ago(9)},
    ])
    seed("goods_receipts", [
        {"grn_number": "GRN2001", "po_number": "PO1001", "received_quantity": 100, "received_at": days_ago(55)},
        {"grn_number": "GRN2002", "po_number": "PO1002", "received_quantity": 50, "received_at": days_ago(50)},
        {"grn_number": "GRN2003", "po_number": "PO1003", "received_quantity": 20, "received_at": days_ago(35)},
    ])
    supplier_invoices = [
        {"invoice_number": "INV3001", "supplier_id": "SUP001", "po_number": "PO1001", "amount": 12000, "quantity": 100, "received_at": days_ago(52)},
        {"invoice_number": "INV3002", "supplier_id": "SUP002", "po_number": "PO1002", "amount": 45000, "quantity": 50, "received_at": days_ago(48)},
        {"invoice_number": "INV3003", "supplier_id": "SUP003", "po_number": "PO1003", "amount": 195000, "quantity": 20, "received_at": days_ago(33)},  # PR-013 violation: price doesn't match PO
        {"invoice_number": "INV3004", "supplier_id": "SUP001", "po_number": None, "amount": 3000, "quantity": 10, "received_at": days_ago(5)},  # PR-004 violation: no PO
        {"invoice_number": "INV3001", "supplier_id": "SUP001", "po_number": "PO1001", "amount": 12000, "quantity": 100, "received_at": days_ago(52)},  # PR-006 violation: duplicate
        {"invoice_number": "INV3005", "supplier_id": "SUP002", "po_number": "PO1002", "amount": 45000, "quantity": 80, "received_at": days_ago(45)},  # PR-012 violation: qty exceeds GRN
    ]
    seed("supplier_invoices", supplier_invoices)
    seed("invoice_approvals", [
        {"invoice_number": "INV3001", "approved_by": "EMP009", "approved_at": days_ago(51)},
        {"invoice_number": "INV3002", "approved_by": "EMP009", "approved_at": days_ago(47)},
        # INV3003/INV3004 have no approval — PR-010 violation
    ])
    payments = [
        {"payment_id": "PAY4001", "invoice_number": "INV3001", "supplier_id": "SUP001", "amount": 12000, "paid_by": "EMP009", "paid_at": days_ago(45)},
        {"payment_id": "PAY4002", "invoice_number": "INV3002", "supplier_id": "SUP002", "amount": 45000, "paid_by": "EMP009", "paid_at": days_ago(40)},
        {"payment_id": "PAY4003", "invoice_number": "INV3004", "supplier_id": "SUP001", "amount": 3000, "paid_by": "EMP009", "paid_at": days_ago(4)},  # PR-014 violation: paid before receipt
        {"payment_id": "PAY4004", "invoice_number": "INV3001", "supplier_id": "SUP001", "amount": 12000, "paid_by": "EMP009", "paid_at": days_ago(44)},  # PR-017 violation: duplicate payment
        {"payment_id": "PAY4005", "invoice_number": None, "supplier_id": "SUP004", "amount": 6000, "paid_by": "EMP009", "paid_at": days_ago(3)},  # PR-016/PR-020 violation: inactive supplier
    ]
    seed("payments", payments)
    seed("payment_approvals", [
        {"payment_id": "PAY4001", "approved_by": "EMP012", "approved_at": days_ago(45)},
        {"payment_id": "PAY4002", "approved_by": "EMP012", "approved_at": days_ago(40)},
        # PAY4003/PAY4005 unapproved — PR-015 violation
    ])


def seed_financial_gl() -> None:
    print("Financial / General Ledger:")
    seed("accounting_periods", [
        {"period": "2026-07", "start_date": datetime(2026, 7, 1, tzinfo=timezone.utc), "end_date": datetime(2026, 7, 31, tzinfo=timezone.utc), "status": "closed"},
        {"period": "2026-08", "start_date": datetime(2026, 8, 1, tzinfo=timezone.utc), "end_date": datetime(2026, 8, 31, tzinfo=timezone.utc), "status": "closed"},
        {"period": "2026-09", "start_date": datetime(2026, 9, 1, tzinfo=timezone.utc), "end_date": datetime(2026, 9, 30, tzinfo=timezone.utc), "status": "open"},
    ])
    journal_entries = [
        {"journal_id": "JE5001", "description": "Monthly depreciation", "amount": 25000, "account": "6100", "prepared_by": "EMP001", "posted_at": days_ago(20), "period": "2026-08", "entry_type": "system"},
        {"journal_id": "JE5002", "description": "Accrual reversal", "amount": 18000, "account": "2200", "prepared_by": "EMP009", "posted_at": days_ago(18), "period": "2026-08", "entry_type": "manual"},
        {"journal_id": "JE5003", "description": "Manual correction — supplier reclass", "amount": 95000, "account": "2100", "prepared_by": "EMP001", "posted_at": days_ago(35), "period": "2026-07", "entry_type": "manual"},  # GL-004 violation: posted after period close
        {"journal_id": "JE5004", "description": "Round-sum adjustment", "amount": 50000, "account": "9999", "prepared_by": "EMP012", "posted_at": days_ago(5), "period": "2026-09", "entry_type": "manual"},  # GL-006 violation: suspicious round amount, suspense account
        {"journal_id": "JE5005", "description": "Round-sum adjustment", "amount": 50000, "account": "9999", "prepared_by": "EMP012", "posted_at": days_ago(5), "period": "2026-09", "entry_type": "manual"},  # GL-005 violation: duplicate pattern
        {"journal_id": "JE5006", "description": "Revenue recognition", "amount": 310000, "account": "4000", "prepared_by": "EMP009", "posted_at": days_ago(3), "period": "2026-09", "entry_type": "manual"},  # GL-007 violation: above threshold, needs extra approval
    ]
    seed("journal_entries", journal_entries)
    seed("journal_approvals", [
        {"journal_id": "JE5001", "approved_by": "EMP012", "approved_at": days_ago(19)},
        {"journal_id": "JE5002", "approved_by": "EMP012", "approved_at": days_ago(17)},
        {"journal_id": "JE5003", "approved_by": "EMP012", "approved_at": days_ago(34)},
        # JE5004/5005/5006 unapproved — GL-001 violation
    ])
    seed("journal_lines", [
        {"journal_id": "JE5001", "line": 1, "debit": 25000, "credit": 0},
        {"journal_id": "JE5001", "line": 2, "debit": 0, "credit": 25000},
        {"journal_id": "JE5002", "line": 1, "debit": 18000, "credit": 0},
        {"journal_id": "JE5002", "line": 2, "debit": 0, "credit": 17000},  # GL-009 violation: doesn't balance
    ])
    seed("general_ledger", [
        {"account": "9999", "account_name": "Suspense", "balance": 50000, "as_of": days_ago(1)},  # GL-008: unresolved suspense balance
        {"account": "1200", "account_name": "Accounts Receivable", "balance": 890000, "as_of": days_ago(1)},
        {"account": "2100", "account_name": "Accounts Payable", "balance": 245000, "as_of": days_ago(1)},
        {"account": "1300", "account_name": "Inventory", "balance": 610000, "as_of": days_ago(1)},
    ])
    seed("ap_transactions", [{"transaction_id": "APT001", "supplier_id": "SUP001", "amount": 12000, "posted_at": days_ago(45)}])
    seed("ar_transactions", [{"transaction_id": "ART001", "customer_id": "CUS001", "amount": 65000, "posted_at": days_ago(30)}])
    seed("inventory", [{"item_code": "ITM001", "description": "Steel brackets", "quantity_on_hand": 4200, "unit_cost": 45.5}])
    # payroll seeded in seed_payroll() — GL-010 reconciles to it too


def seed_payroll() -> None:
    print("Payroll:")
    payroll = [
        {"pay_id": "PAY-E001", "employee_id": "EMP001", "period": "2026-08", "gross_pay": 42000, "net_pay": 31500},
        {"pay_id": "PAY-E002", "employee_id": "EMP002", "period": "2026-08", "gross_pay": 55000, "net_pay": 40200},
        {"pay_id": "PAY-E004", "employee_id": "EMP004", "period": "2026-08", "gross_pay": 38000, "net_pay": 28600},  # PY-002 violation: terminated 45 days ago, still paid
        {"pay_id": "PAY-E999", "employee_id": "EMP999", "period": "2026-08", "gross_pay": 30000, "net_pay": 22500},  # PY-009 violation: ghost employee, no HR record
        {"pay_id": "PAY-E006", "employee_id": "EMP006", "period": "2026-08", "gross_pay": 33000, "net_pay": 24800},
    ]
    seed("payroll", payroll)
    seed("payroll_history", [
        {"employee_id": "EMP001", "period": "2026-07", "gross_pay": 40000},
        {"employee_id": "EMP001", "period": "2026-08", "gross_pay": 42000},
        {"employee_id": "EMP006", "period": "2026-07", "gross_pay": 33000},
        {"employee_id": "EMP006", "period": "2026-08", "gross_pay": 58000},  # PY-008 violation: unexplained jump
    ])
    seed("employee_approvals", [
        {"employee_id": "EMP001", "approved_by": "EMP005", "approved_at": days_ago(900)},
        {"employee_id": "EMP012", "approved_by": "EMP005", "approved_at": days_ago(1100)},
        # EMP006/EMP010 (recent hires) have no approval on file — PY-003 violation
    ])
    seed("salary_changes", [
        {"change_id": "SAL001", "employee_id": "EMP006", "old_salary": 33000, "new_salary": 58000, "changed_at": days_ago(10)},
    ])
    seed("salary_approvals", [])  # SAL001 unapproved — PY-004 violation
    seed("bank_accounts", [
        {"employee_id": "EMP001", "account_number": "900011"},
        {"employee_id": "EMP009", "account_number": "900019"},
        {"employee_id": "EMP012", "account_number": "900019"},  # PY-006 violation: same account as EMP009
    ])
    seed("employee_documents", [
        {"employee_id": "EMP001", "document_type": "ID copy", "on_file": True},
        {"employee_id": "EMP001", "document_type": "signed contract", "on_file": True},
        # EMP999 (ghost) has no documents at all — reinforces PY-009
    ])
    seed("payroll_changes", [{"change_id": "PC001", "employee_id": "EMP006", "field": "salary", "changed_at": days_ago(10)}])
    seed("audit_logs", [
        {"log_id": "AL001", "entity_type": "salary_changes", "entity_id": "SAL001", "action": "created", "logged_at": days_ago(10)},
        # supplier_bank_changes SBC002 has no matching audit_logs row — MD-005 violation
    ])


def seed_master_data() -> None:
    print("Master Data Management:")
    seed("customers", CUSTOMERS)
    seed("customer_approvals", [
        {"customer_id": "CUS001", "approved_by": "EMP009", "approved_at": days_ago(600)},
        {"customer_id": "CUS002", "approved_by": "EMP009", "approved_at": days_ago(550)},
        {"customer_id": "CUS003", "approved_by": "EMP009", "approved_at": days_ago(300)},
        # CUS004 has no approval — MD-006 violation
    ])
    seed("credit_approvals", [
        {"customer_id": "CUS001", "credit_limit": 500000, "approved_by": "EMP012", "approved_at": days_ago(600)},
        {"customer_id": "CUS002", "credit_limit": 1200000, "approved_by": "EMP012", "approved_at": days_ago(550)},
        # CUS003's 250000 limit has no approval record — MD-008 violation
    ])


def seed_order_to_cash() -> None:
    print("Order-to-Cash:")
    sales_orders = [
        {"order_id": "SO6001", "customer_id": "CUS001", "amount": 45000, "created_at": days_ago(30)},
        {"order_id": "SO6002", "customer_id": "CUS002", "amount": 1450000, "created_at": days_ago(25)},  # OTC-003 violation: exceeds credit limit
        {"order_id": "SO6003", "customer_id": "CUS004", "amount": 20000, "created_at": days_ago(5)},  # OTC-002/009 violation: inactive customer
        {"order_id": "SO6004", "customer_id": "CUS001", "amount": 15000, "created_at": days_ago(10)},
    ]
    seed("sales_orders", sales_orders)
    seed("sales_approvals", [
        {"order_id": "SO6001", "approved_by": "EMP010", "approved_at": days_ago(29)},
        {"order_id": "SO6004", "approved_by": "EMP010", "approved_at": days_ago(9)},
        # SO6002/SO6003 unapproved — OTC-001 violation
    ])
    sales_invoices = [
        {"invoice_id": "SI7001", "order_id": "SO6001", "customer_id": "CUS001", "amount": 45000, "issued_at": days_ago(28)},
        {"invoice_id": "SI7002", "order_id": None, "customer_id": "CUS002", "amount": 12000, "issued_at": days_ago(15)},  # OTC-004 violation: no delivery
        {"invoice_id": "SI7001", "order_id": "SO6001", "customer_id": "CUS001", "amount": 45000, "issued_at": days_ago(28)},  # OTC-005 violation: duplicate
    ]
    seed("sales_invoices", sales_invoices)
    seed("deliveries", [{"delivery_id": "DEL8001", "invoice_id": "SI7001", "delivered_at": days_ago(27)}])
    seed("discount_approvals", [])  # SO6002 has an implied large discount with none on file — OTC-006
    seed("customer_receipts", [
        {"receipt_id": "REC9001", "customer_id": "CUS001", "amount": 45000, "invoice_id": "SI7001", "received_at": days_ago(20)},
        {"receipt_id": "REC9002", "customer_id": "CUS002", "amount": 12000, "invoice_id": None, "received_at": days_ago(10)},  # OTC-007 violation: unallocated
    ])
    seed("receivables", [
        {"customer_id": "CUS002", "outstanding_amount": 1450000, "days_overdue": 95},  # OTC-008: bad-debt risk
        {"customer_id": "CUS001", "outstanding_amount": 0, "days_overdue": 0},
    ])


def seed_change_management() -> None:
    print("Change Management:")
    seed("change_requests", [
        {"change_id": "CR001", "description": "Upgrade payment gateway", "requested_by": "EMP002", "requested_at": days_ago(60), "scheduled_window": "weekend"},
        {"change_id": "CR002", "description": "Patch ERP module", "requested_by": "EMP011", "requested_at": days_ago(20), "scheduled_window": "weekend"},
        {"change_id": "CR003", "description": "Emergency hotfix — payroll calc bug", "requested_by": "EMP002", "requested_at": days_ago(5), "scheduled_window": "emergency"},
    ])
    seed("change_approvals", [
        {"change_id": "CR001", "approved_by": "EMP011", "approved_at": days_ago(58)},
        # CR003 (emergency) has no retrospective approval yet — CM-003 violation
    ])
    seed("production_changes", [
        {"change_id": "CR001", "deployed_at": days_ago(55), "deployed_by": "EMP002"},
        {"change_id": "CR003", "deployed_at": days_ago(5), "deployed_by": "EMP002"},
        {"change_id": None, "deployed_at": days_ago(2), "deployed_by": "EMP007"},  # CM-007 violation: unauthorized change, no request at all (also by a terminated employee)
    ])
    seed("test_results", [{"change_id": "CR001", "result": "pass", "tested_at": days_ago(56)}])  # CR002 deployed without test evidence — CM-002 violation
    seed("code_changes", [{"change_id": "CR001", "committed_by": "EMP002"}])
    seed("deployments", [
        {"change_id": "CR001", "deployed_by": "EMP002", "deployed_at": days_ago(55)},  # CM-004: same person committed and deployed
        {"change_id": "CR002", "deployed_by": "EMP011", "deployed_at": datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc)},  # CM-006 violation: weekday afternoon, outside approved window
    ])
    seed("production_logs", [
        {"log_id": "PL001", "change_id": "CR001", "logged_at": days_ago(55)},
        {"log_id": "PL002", "change_id": None, "logged_at": days_ago(2)},  # matches the unauthorized production_changes row
    ])


def seed_it_operations() -> None:
    print("IT Operations:")
    seed("scheduled_jobs", [
        {"job_id": "JOB001", "job_name": "Nightly GL close", "schedule": "daily 02:00", "critical": True},
        {"job_id": "JOB002", "job_name": "Payroll export", "schedule": "monthly", "critical": True},
        {"job_id": "JOB003", "job_name": "Archive old logs", "schedule": "weekly", "critical": False},
    ])
    seed("job_executions", [
        {"job_id": "JOB001", "executed_at": days_ago(1), "status": "success", "duration_seconds": 340},
        {"job_id": "JOB001", "executed_at": days_ago(2), "status": "failed", "duration_seconds": 12},  # OP-001 violation
        {"job_id": "JOB002", "executed_at": days_ago(35), "status": "success", "duration_seconds": 8200},  # OP-004: abnormally long
        # JOB003 missing an expected run entirely — OP-003 violation
    ])
    seed("incident_records", [])  # the JOB001 failure has no follow-up incident — OP-002 violation
    seed("incidents", [
        {"incident_id": "INC001", "description": "Payment gateway timeout", "severity": "high", "opened_at": days_ago(30), "status": "open"},  # OP-006 violation: overdue
        {"incident_id": "INC002", "description": "Disk space warning", "severity": "low", "opened_at": days_ago(3), "status": "resolved"},
    ])
    seed("system_logs", [
        {"log_id": "SL001", "system": "erp-prod-01", "logged_at": days_ago(1)},
        {"log_id": "SL002", "system": "erp-prod-01", "logged_at": days_ago(400)},  # OP-007 violation: exceeds retention rule below
    ])
    seed("log_retention_rules", [{"system": "erp-prod-01", "retention_days": 365}])


def seed_backup_recovery() -> None:
    print("Backup & Recovery:")
    seed("systems", [
        {"system_id": "SYS001", "system_name": "erp-prod-01", "criticality": "critical", "owner": "EMP002"},
        {"system_id": "SYS002", "system_name": "crm-prod-01", "criticality": "critical", "owner": "EMP011"},
        {"system_id": "SYS003", "system_name": "reporting-01", "criticality": "medium", "owner": "EMP011"},
        {"system_id": "SYS004", "system_name": "legacy-billing", "criticality": "critical", "owner": "EMP002"},  # BK-001 violation: no backup job below
    ])
    seed("backup_jobs", [
        {"backup_id": "BKJ001", "system_id": "SYS001", "run_at": days_ago(1), "status": "success"},
        {"backup_id": "BKJ002", "system_id": "SYS002", "run_at": days_ago(1), "status": "success"},
        {"backup_id": "BKJ003", "system_id": "SYS003", "run_at": days_ago(9), "status": "failed"},  # BK-003 violation
    ])
    seed("backup_schedules", [
        {"system_id": "SYS001", "frequency": "daily"},
        {"system_id": "SYS002", "frequency": "daily"},
        {"system_id": "SYS003", "frequency": "daily"},  # BK-002 violation: scheduled daily, last run 9 days ago
    ])
    seed("backup_incidents", [])  # BKJ003 failure has no logged incident — BK-003 reinforced
    seed("backup_retention_rules", [{"system_id": "SYS001", "retention_days": 30}])
    seed("recovery_tests", [
        {"system_id": "SYS001", "tested_at": days_ago(80), "result": "pass"},
        {"system_id": "SYS002", "tested_at": days_ago(400), "result": "fail"},  # BK-005/BK-006 violation: stale + failed
        # SYS003/SYS004 have never had a recovery test — BK-005 violation
    ])


def seed_it_asset_management() -> None:
    print("IT Asset Management:")
    seed("asset_register", [
        {"asset_id": "AST001", "asset_name": "Laptop-EMP001", "assigned_to": "EMP001", "status": "in_use"},
        {"asset_id": "AST002", "asset_name": "Laptop-EMP004", "assigned_to": "EMP004", "status": "in_use"},  # AS-003 violation: assigned to terminated employee
        {"asset_id": "AST003", "asset_name": "Laptop-EMP999", "assigned_to": "EMP999", "status": "in_use"},  # AS-002 violation: no matching HR record
        {"asset_id": "AST004", "asset_name": "Server-reporting-01", "assigned_to": None, "status": "in_use"},
        # a system with no asset_register row at all — AS-001 violation (see system_inventory)
    ])
    seed("system_inventory", [
        {"system_id": "SYS001", "os": "Windows Server 2019", "version": "10.0.17763"},
        {"system_id": "SYS003", "os": "Ubuntu", "version": "18.04"},  # AS-005/PM-003 violation: EOL version
        {"system_id": "SYS005", "os": "Windows Server 2022", "version": "10.0.20348"},  # no asset_register row — AS-001
    ])
    seed("software_inventory", [
        {"asset_id": "AST001", "software_name": "Microsoft Office", "version": "2021"},
        {"asset_id": "AST001", "software_name": "uTorrent", "version": "3.5"},  # AS-004 violation: not on approved list
        {"asset_id": "AST002", "software_name": "Slack", "version": "4.30"},
    ])
    seed("approved_software", [
        {"software_name": "Microsoft Office", "risk_level": "low"},
        {"software_name": "Slack", "risk_level": "low"},
    ])
    seed("support_matrix", [
        {"os": "Windows Server 2019", "supported_until": days_from_now(400)},
        {"os": "Ubuntu 18.04", "supported_until": days_ago(200)},  # confirms AS-005 violation
        {"os": "Windows Server 2022", "supported_until": days_from_now(900)},
    ])


def seed_data_privacy() -> None:
    print("Data Privacy:")
    seed("data_access", [
        {"access_id": "DA001", "user_id": "U001", "data_classification": "personal_info", "granted_by": "EMP005", "granted_at": days_ago(300)},
        {"access_id": "DA002", "user_id": "U012", "data_classification": "personal_info", "granted_by": None, "granted_at": days_ago(6)},  # DP-001 violation: no authorization on file
    ])
    seed("data_classification", [
        {"dataset": "hr_employees", "classification": "personal_info"},
        {"dataset": "customer_receipts", "classification": "financial"},
    ])
    seed("user_access", [
        {"user_id": "U013", "dataset": "hr_employees", "access_level": "full"},  # DP-003 violation: generic account has excessive access
    ])
    seed("data_records", [
        {"record_id": "DR001", "subject": "former applicant", "created_at": days_ago(2500)},  # DP-004 violation: beyond retention
        {"record_id": "DR002", "subject": "current employee", "created_at": days_ago(300)},
    ])
    seed("retention_rules", [{"dataset": "applicant_records", "retention_days": 730}])
    seed("privacy_requests", [
        {"request_id": "PRQ001", "type": "deletion", "requested_at": days_ago(60)},
        {"request_id": "PRQ002", "type": "deletion", "requested_at": days_ago(20)},  # DP-005 violation: no deletion_records match below
    ])
    seed("deletion_records", [{"request_id": "PRQ001", "deleted_at": days_ago(55)}])


def seed_encryption() -> None:
    print("Encryption Controls:")
    seed("data_assets", [
        {"asset_name": "customer_pii_db", "sensitivity": "high"},
        {"asset_name": "archived_backups", "sensitivity": "high"},  # EN-001 violation: no encryption_config below
    ])
    seed("encryption_config", [{"asset_name": "customer_pii_db", "encrypted": True, "algorithm": "AES-256"}])
    seed("encryption_keys", [
        {"key_id": "KEY001", "purpose": "db_encryption", "expires_at": days_from_now(200), "authorized": True},
        {"key_id": "KEY002", "purpose": "backup_encryption", "expires_at": days_ago(5), "authorized": True},  # EN-002 violation: expired
    ])
    seed("key_management_logs", [{"key_id": "KEY001", "action": "rotated", "logged_at": days_ago(100)}])
    seed("system_configurations", [
        {"system_id": "SYS001", "tls_version": "1.2"},
        {"system_id": "SYS004", "tls_version": "1.0"},  # EN-004 violation: weak protocol
    ])
    seed("security_baselines", [{"control": "min_tls_version", "required_value": "1.2"}])


def seed_certificate_management() -> None:
    print("Certificate Management:")
    seed("certificates", [
        {"cert_id": "CRT001", "system_id": "SYS001", "common_name": "erp.mtaudit.local", "expires_at": days_from_now(180), "issued_to_system": "SYS001"},
        {"cert_id": "CRT002", "system_id": "SYS002", "common_name": "crm.mtaudit.local", "expires_at": days_from_now(12), "issued_to_system": "SYS002"},  # CERT-001 violation: expiring soon
        {"cert_id": "CRT003", "system_id": "SYS003", "common_name": "reports.mtaudit.local", "expires_at": days_ago(5), "issued_to_system": "SYS003"},  # CERT-002 violation: already expired
        {"cert_id": "CRT004", "system_id": None, "common_name": "unknown.mtaudit.local", "expires_at": days_from_now(300), "issued_to_system": None},  # CERT-003 violation: not tied to any known system
    ])
    seed("certificate_renewals", [{"cert_id": "CRT001", "renewed_at": days_ago(185), "renewed_by": "EMP002"}])
    # CRT002/CRT003 have no renewal record on file — CERT-004 violation


def seed_api_controls() -> None:
    print("API Controls:")
    seed("api_inventory", [
        {"api_id": "API001", "api_name": "Payments API", "system_id": "SYS001", "registered": True},
        {"api_id": "API002", "api_name": "Legacy Reporting API", "system_id": None, "registered": False},  # API-001 violation
    ])
    seed("api_access", [
        {"api_id": "API001", "user_id": "U002", "granted_at": days_ago(200)},
        {"api_id": "API001", "user_id": "U012", "granted_at": days_ago(6)},  # API-002 violation: contractor with no formal role/permission on file
    ])
    seed("api_logs", [
        {"api_id": "API001", "status": "auth_failed", "occurred_at": days_ago(1), "source_ip": "203.0.113.5"},
        {"api_id": "API001", "status": "auth_failed", "occurred_at": days_ago(1), "source_ip": "203.0.113.5"},
        {"api_id": "API001", "status": "auth_failed", "occurred_at": days_ago(1), "source_ip": "203.0.113.5"},  # API-003: repeated failures, same source
        {"api_id": "API001", "status": "success", "occurred_at": days_ago(1)},
    ])
    seed("api_configurations", [
        {"api_id": "API001", "tls_enabled": True},
        {"api_id": "API002", "tls_enabled": False},  # API-004 violation
    ])
    seed("api_keys", [
        {"key_id": "APIKEY001", "api_id": "API001", "created_at": days_ago(30), "expires_at": days_from_now(60)},
        {"key_id": "APIKEY002", "api_id": "API001", "created_at": days_ago(500), "expires_at": days_ago(100)},  # API-005 violation: expired, still present
    ])


def seed_vulnerability_management() -> None:
    print("Vulnerability Management:")
    seed("assets", [
        {"asset_id": "AST001", "hostname": "ws-emp001"},
        {"asset_id": "AST004", "hostname": "srv-reporting-01"},
    ])
    seed("vulnerabilities", [
        {"vuln_id": "VUL001", "asset_id": "AST004", "severity": "critical", "discovered_at": days_ago(20), "status": "open"},  # VM-002 violation: over SLA
        {"vuln_id": "VUL002", "asset_id": "AST001", "severity": "medium", "discovered_at": days_ago(5), "status": "open"},
        {"vuln_id": "VUL003", "asset_id": "AST004", "severity": "high", "discovered_at": days_ago(90), "status": "remediated"},
    ])
    seed("remediation_actions", [{"vuln_id": "VUL003", "action": "patched", "completed_at": days_ago(60)}])
    seed("sla_rules", [{"severity": "critical", "remediate_within_days": 7}])
    seed("retests", [])  # VUL003 remediation has no retest on file — VM-004 violation


def seed_network_controls() -> None:
    print("Network Controls:")
    seed("network_connections", [
        {"connection_id": "NC001", "source": "10.0.1.0/24", "destination": "SYS001", "approved": True},
        {"connection_id": "NC002", "source": "203.0.113.0/24", "destination": "SYS001", "approved": False},  # NW-001 violation
    ])
    seed("approved_connections", [{"connection_id": "NC001"}])
    seed("firewall_rules", [
        {"rule_id": "FW001", "source": "10.0.0.0/8", "destination": "SYS001", "port": 443, "action": "allow"},
        {"rule_id": "FW002", "source": "0.0.0.0/0", "destination": "SYS001", "port": 3389, "action": "allow"},  # NW-003 violation: overly permissive (RDP open to internet)
    ])
    seed("firewall_approvals", [{"rule_id": "FW001", "approved_by": "EMP002", "approved_at": days_ago(400)}])
    # FW002 has no approval — NW-002 violation
    seed("network_devices", [
        {"device_id": "NDV001", "hostname": "core-switch-01"},
        {"device_id": "NDV002", "hostname": "rogue-ap-detected"},  # NW-004 violation: not in asset_register
    ])


def seed_web_filtering() -> None:
    print("Web Filtering / Content Security:")
    seed("web_filter_status", [
        {"asset_id": "AST001", "filtering_active": True, "on_network": True},
        {"asset_id": "AST002", "filtering_active": False, "on_network": False},  # WF-001/WF-006/WF-010 violation
    ])
    seed("web_filter_policies", [{"policy_id": "WFP001", "category": "malware", "blocked": True}])
    seed("security_baselines", [{"control": "block_malware_category", "required_value": True}])
    seed("web_filter_logs", [
        {"user_id": "U006", "url": "known-malware-site.example", "action": "blocked", "occurred_at": days_ago(1)},
        {"user_id": "U006", "url": "known-malware-site.example", "action": "blocked", "occurred_at": days_ago(1)},
        {"user_id": "U006", "url": "known-malware-site.example", "action": "blocked", "occurred_at": days_ago(1)},  # WF-004 violation: repeated attempts
    ])
    seed("security_logs", [{"log_id": "SEC001", "event": "blocked_access", "logged_at": days_ago(1)}])
    # one of the three blocked attempts above has no matching security_logs row — WF-003 violation
    seed("web_filter_exceptions", [{"exception_id": "WFE001", "user_id": "U002", "reason": "vendor site whitelisted", "granted_at": days_ago(30)}])
    seed("exception_approvals", [])  # WFE001 has no approval on file — WF-005 violation
    seed("web_filter_config", [{"config_id": "WFC001", "category_db_version": "2026.06", "updated_at": days_ago(120)}])  # WF-007 violation: stale vs patch_releases below


def seed_patch_management() -> None:
    print("Patch Management:")
    seed("patch_inventory", [
        {"asset_id": "AST001", "patch_id": "KB5001716", "installed": True, "installed_at": days_ago(10)},
        {"asset_id": "AST004", "patch_id": "KB5001716", "installed": False, "installed_at": None},  # PM-001/PM-002/PM-008 violation
    ])
    seed("patch_releases", [
        {"patch_id": "KB5001716", "severity": "critical", "released_at": days_ago(40)},
    ])
    seed("sla_rules", [{"severity": "critical", "patch_within_days": 14}])
    seed("patch_catalogue", [{"patch_id": "KB5001716", "applies_to_os": "Windows Server 2019"}])
    seed("patch_deployments", [
        {"deployment_id": "PD001", "patch_id": "KB5001716", "asset_id": "AST001", "deployed_at": days_ago(10), "approved": True, "tested": True},
        {"deployment_id": "PD002", "patch_id": "KB5001716", "asset_id": "AST002", "deployed_at": days_ago(8), "approved": False, "tested": False},  # PM-004/PM-005 violation
        {"deployment_id": "PD003", "patch_id": "KB5001716", "asset_id": "AST003", "deployed_at": days_ago(1), "approved": True, "tested": True, "emergency": True},  # PM-006: needs retrospective approval tracking
    ])
    seed("patch_approvals", [
        {"deployment_id": "PD001", "approved_by": "EMP002", "approved_at": days_ago(11)},
        {"deployment_id": "PD003", "approved_by": "EMP002", "approved_at": days_ago(1)},
    ])
    seed("patch_incidents", [{"deployment_id": "PD002", "issue": "failed install, agent unresponsive", "resolved": False}])  # PM-007 violation: unresolved
    seed("patch_exceptions", [{"asset_id": "AST004", "reason": "vendor compatibility hold"}])
    seed("exception_approvals", [])  # patch exception for AST004 unapproved — PM-010 violation


def seed_remote_access() -> None:
    print("Remote Access:")
    seed("remote_access_logs", [
        {"user_id": "U002", "session_id": "RS001", "started_at": days_ago(1), "mfa_used": True, "source_country": "ZA", "idle_minutes": 5},
        {"user_id": "U007", "session_id": "RS002", "started_at": days_ago(3), "mfa_used": False, "source_country": "ZA", "idle_minutes": 200},  # RA-001/RA-004/RA-010 violation: terminated employee, no MFA, long idle
        {"user_id": "U011", "session_id": "RS003", "started_at": days_ago(1), "mfa_used": True, "source_country": "RU", "idle_minutes": 10},  # RA-006 violation: unusual geography
    ])
    seed("mfa_config", [
        {"user_id": "U002", "mfa_enabled": True},
        {"user_id": "U007", "mfa_enabled": False},
    ])
    seed("remote_access_grants", [
        {"user_id": "U002", "granted_at": days_ago(200), "approved": True},
        {"user_id": "U007", "granted_at": days_ago(700), "approved": True},  # still active despite termination — reinforces RA-004
    ])
    seed("vpn_accounts", [
        {"user_id": "U002", "vpn_username": "vpn.jvandermerwe", "status": "active"},
        {"user_id": "U007", "vpn_username": "vpn.lmokoena", "status": "active"},  # RA-004 violation
        {"user_id": "U003", "vpn_username": "vpn.pgovender", "status": "active"},
    ])
    seed("session_audit", [{"session_id": "RS001", "logged": True}])
    # RS002/RS003 have no matching session_audit entry — RA-005 violation
    seed("access_policies", [{"policy_id": "AP001", "allowed_countries": ["ZA"], "allowed_hours": "06:00-20:00"}])
    seed("privileged_access_approvals", [])  # U011's session used a privileged role with no approval on file — RA-007 violation
    seed("vendor_access_grants", [
        {"vendor": "ERP Support Vendor", "granted_at": days_ago(400), "expires_at": days_ago(30)},  # RA-008 violation: grant expired but presumably still usable
    ])
    seed("remote_access_tools", [
        {"tool_name": "Company VPN Client", "in_use_on": "AST001"},
        {"tool_name": "AnyDesk", "in_use_on": "AST002"},  # RA-009 violation: not an approved remote tool
    ])
    seed("session_policies", [{"policy_id": "SP001", "max_idle_minutes": 30}])
    seed("vpn_config", [{"config_id": "VC001", "split_tunnel_enabled": True}])  # RA-012 violation, per network_policies below
    seed("network_policies", [{"policy_id": "NP001", "split_tunnel_allowed": False}])


if __name__ == "__main__":
    main()
