"""Give the always-on, device-driven software compliance check its own
control code (SW-001) instead of reusing the control library's separate,
real AS-004 entry.

=== The bug ===
software_compliance_service.py auto-creates a Control/AuditTest the
moment a device first reports installed software — no human activates
it, it just starts. To give that auto-created control a sensible name
without inventing a duplicate library row, an earlier version reused
the control library's own AS-004 ("Unauthorised software should be
identified") — same control_code, and the auto-created Control even
copied AS-004's control_library_id. That library entry has its own
required_tables and a generic-engine rule_definition (missing_match,
software_inventory -> approved_software) meant for a client's OWN
separate database. Sharing the code meant the always-on, fully-
automatic device control inherited that unrelated mapping/rule-template
machinery too — the Controls/mapping screen showed "bind these tables"
for a control that already works with no mapping at all, exactly the
confusion the user reported.

app/services/software_compliance_service.py's code (this same PR) now
uses "SW-001" — its own standalone code, same pattern as EP-001 (also
not one of the 157 cataloged controls). This migration is the one-time
fix for data already created under the old code: exactly one real row,
Bidvest ALICE's device software-compliance control (identified by
test_type='endpoint_compliance', which only the two device-driven
services ever set, combined with the old test_code — this is NOT a
blanket "every AS-004 row" rename, which would incorrectly catch the
control library's own, separate, legitimate AS-004 if any organization
ever activates it normally).

Downgrade is intentionally a no-op: reverting the application code
without reverting this data would immediately reproduce the bug for
this one row, and there is no way to distinguish "was already SW-001
before this migration" from "became SW-001 because of it" after the
fact to safely reverse just this row.

Revision ID: 0072
Revises: 0071
Create Date: 2026-09-18
"""
from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE audit_tests
        SET test_code = 'SW-001'
        WHERE test_code = 'AS-004' AND test_type = 'endpoint_compliance'
        """
    )
    # Also refreshes control_name/control_description from the test's own
    # fields (what _ensure_control_link would set on a fresh creation) —
    # the existing row's name was copied from the library's AS-004 entry
    # at creation time and, being self-healing-on-missing-link rather
    # than self-correcting, would otherwise keep showing that unrelated
    # name forever.
    op.execute(
        """
        UPDATE controls c
        SET control_code = 'SW-001', control_library_id = NULL,
            control_name = t.test_name, control_description = t.test_description
        FROM control_audit_tests cat
        JOIN audit_tests t ON t.audit_test_id = cat.audit_test_id
        WHERE c.control_id = cat.control_id
          AND c.control_code = 'AS-004'
          AND t.test_code = 'SW-001' AND t.test_type = 'endpoint_compliance'
        """
    )


def downgrade() -> None:
    pass
