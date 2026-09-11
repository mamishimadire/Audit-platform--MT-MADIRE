"""Initial schema — executes database/schema.sql verbatim

schema.sql is the single source of truth for Version 1's table structure
(see MASTER BUILD INSTRUCTION, Section 1). Rather than hand-transcribing it
into op.create_table() calls — which risks silent drift between the
"authoritative" file and what Alembic actually applies — this migration
reads and executes that file directly.

Revision ID: 0001
Revises:
Create Date: 2026-09-05

"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# backend/alembic/versions/0001_initial_schema.py -> ../../../database/schema.sql
SCHEMA_SQL_PATH = Path(__file__).resolve().parents[3] / "database" / "schema.sql"

# Reverse of creation order in schema.sql, so FK dependents drop before their targets.
_TABLES_NEWEST_FIRST = [
    "audit_logs",
    "retests",
    "remediation_actions",
    "finding_root_causes",
    "findings",
    "exception_records",
    "exceptions",
    "evidence",
    "test_executions",
    "monitoring_schedules",
    "test_rules",
    "test_data_mappings",
    "control_audit_tests",
    "audit_tests",
    "data_fields",
    "data_entities",
    "data_connections",
    "data_sources",
    "gateways",
    "risk_controls",
    "controls",
    "risks",
    "risk_categories",
    "process_activities",
    "business_processes",
    "user_sessions",
    "role_permissions",
    "user_roles",
    "permissions",
    "roles",
    "users",
    "business_units",
    "organization_settings",
    "organizations",
]


def upgrade() -> None:
    op.execute(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))


def downgrade() -> None:
    for table in _TABLES_NEWEST_FIRST:
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
