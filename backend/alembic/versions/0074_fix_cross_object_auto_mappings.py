"""Backfills the one real casualty of the cross-object auto-accept bug
fixed in app/core/canonical_model.py this same PR (suggest_canonical_field
+ the new payments.status canonical field).

=== The bug, and why this row is corrupted ===
A user-reported screenshot showed Bidvest ALICE's SOD-001 "payments"
entity mapping its own "status" column to "user.status" at 100%
confidence, auto-accepted. Root cause: suggest_canonical_field's
preferred-object branch (infer_object_for_entity("payments") ->
"payments") found no good match for "status" among payments' OWN
canonical fields, because "payments" had no status-like field in
CANONICAL_MODEL at all — so it fell through to a fully unconstrained
global search across every canonical object, which found an EXACT
literal match on a totally unrelated one ("user.status") and returned it
at full, auto-accept-eligible confidence with no signal that it had
crossed into the wrong object. Fixed two ways in this same PR: (1)
CANONICAL_MODEL["payments"] now has its own "status" field, so a real
payment-status column resolves in-object going forward; (2) a global
fallback match that lands on a DIFFERENT object than a known
preferred_object is now capped well under AUTO_ACCEPT_THRESHOLD
(_CROSS_OBJECT_FALLBACK_CAP), so even a genuinely unrelated field can
never silently auto-accept into the wrong object again.

Confirmed by a full scan of every mapping_status='auto' row across every
organization in the shared database: this is the ONLY row where the
mapped object differs from infer_object_for_entity's read of the row's
own source entity name. approved_by is null on it — it was accepted
purely by the (buggy) algorithm, never explicitly reviewed by a person —
and no test_rule for this audit_test (SOD-001, a role_permissions
self-join) reads either user.status or payments.status, so correcting it
changes no rule's live behavior, only the mapping display itself.

Revision ID: 0074
Revises: 0073
Create Date: 2026-09-18
"""
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE test_data_mappings tdm
        SET canonical_field = 'payments.status'
        FROM data_fields df, data_entities de
        WHERE tdm.field_id = df.field_id
          AND df.entity_id = de.entity_id
          AND de.entity_name = 'payments'
          AND df.field_name = 'status'
          AND tdm.canonical_field = 'user.status'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE test_data_mappings tdm
        SET canonical_field = 'user.status'
        FROM data_fields df, data_entities de
        WHERE tdm.field_id = df.field_id
          AND df.entity_id = de.entity_id
          AND de.entity_name = 'payments'
          AND df.field_name = 'status'
          AND tdm.canonical_field = 'payments.status'
        """
    )
