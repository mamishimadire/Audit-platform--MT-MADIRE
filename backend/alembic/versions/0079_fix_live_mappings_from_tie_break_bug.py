"""Corrects 7 live mapping rows discovered by re-running every existing
auto/approved mapping across every organization through the current
suggest_canonical_field/infer_object_for_entity — a full "reconfirm all
mappings" pass requested after fixing infer_object_for_entity's
tie-breaking bug (an exact-match table name losing to a longer,
unrelated superset sharing the same tokens — see the migration that
fixed DP-003/DP-004's own required_tables for the full explanation).

All 7 are Bidvest ALICE rows created (or, for 3 of them, human-approved)
before that fix existed, where the entity's own name was itself the
correct canonical object but the buggy tie-break sent it to a longer,
wrong one instead:

  certificates.system_id   -> was system.system_id       (3 rows)
  system_users._id         -> was system.system_id       (1 row, also
                               not an infer_object_for_entity case —
                               system_users aliases to "user" via
                               _ENTITY_NAME_ALIASES, unaffected by the
                               tie-break fix — this one is simply
                               historical: a technical _id column
                               scored a primary-key boost that today's
                               _SYNTHETIC_ID_FIELD_NAMES exclusion would
                               never allow. Corrected to what today's
                               algorithm actually recommends: user.user_id.)
  retention_rules.retention_days -> was log_retention_rules.retention_days
  user_access.user_id       -> was user_access_changes.user_id
  approvals.approved_by     -> was supplier_approvals.approved_by
  approvals.approved_at     -> was supplier_approvals.approved_at

Verified none of these organization's currently-active rules read
through the WRONG value either way (checked each control's test_rules
row directly before writing this) — CERT-001/CERT-004 need certificates.
expires_at/cert_id, not system_id; AC-002 needs employee.employment_
status/user.status, not user_id; MD-003's rule reads supplier_bank_
changes directly, not the "approvals" canonical object at all; DP-003/
DP-004 have no active rule yet. Correcting canonical_field only —
mapping_status/confidence_score/approved_by are left as they were (3 of
these 7 were explicitly human-approved; that approval decision itself
isn't being second-guessed, only the field it was approving).

Revision ID: 0079
Revises: 0078
Create Date: 2026-09-18
"""
from alembic import op
from sqlalchemy import text

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None

_CORRECTIONS = {
    "42effcf6-e854-4ecf-bb57-2bcabe4b52de": ("system.system_id", "certificates.system_id"),
    "0157c0b9-c781-4514-8d42-54dcddcc0412": ("system.system_id", "certificates.system_id"),
    "dfe928ba-d40c-47c1-986a-77d4ab115950": ("system.system_id", "user.user_id"),
    "89314de9-3d30-4abc-b2c8-7a297f268099": ("log_retention_rules.retention_days", "retention_rules.retention_days"),
    "c9e93e27-86b3-4beb-9bda-ecb008644234": ("user_access_changes.user_id", "user_access.user_id"),
    "f1294c62-1a4a-4a6b-a8e9-35de7ab913fd": ("supplier_approvals.approved_by", "approvals.approved_by"),
    "d87e38f0-9544-4c39-b5f0-ab7176350174": ("supplier_approvals.approved_at", "approvals.approved_at"),
}


def upgrade() -> None:
    bind = op.get_bind()
    for mapping_id, (_old, new) in _CORRECTIONS.items():
        bind.execute(
            text("UPDATE test_data_mappings SET canonical_field = :new WHERE mapping_id = :mapping_id"),
            {"new": new, "mapping_id": mapping_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for mapping_id, (old, _new) in _CORRECTIONS.items():
        bind.execute(
            text("UPDATE test_data_mappings SET canonical_field = :old WHERE mapping_id = :mapping_id"),
            {"old": old, "mapping_id": mapping_id},
        )
