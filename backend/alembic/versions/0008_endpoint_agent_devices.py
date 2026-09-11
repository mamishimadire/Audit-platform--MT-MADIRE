"""Endpoint Agent: devices + device_telemetry, and devices:manage permission

New schema domain for the Endpoint Agent MVP (user-selected priority,
2026-09-05) — a Windows agent for managed corporate devices, architecturally
separate from the Gateway (which reaches a client's own database) because a
device reports its own security telemetry directly to the platform; there is
no external database to reach through, so compliance checks run server-side
against this data immediately, rather than being pulled by a Gateway.

  devices           — one row per enrolled device, mirroring the `gateways`
                       table's registration-code lifecycle exactly (single-use
                       code, 15-min expiry, tenant-bound, device certificate
                       fingerprint only — never a raw credential stored).
  device_telemetry  — one row per heartbeat/telemetry report: AV, firewall,
                       disk encryption, OS-patch status, plus the raw payload
                       for anything not yet promoted to a first-class column.

A non-compliant device becomes a real `exceptions` row under a well-known
"Endpoint Security Compliance" audit_test (auto-created per organization),
reusing the existing audit_test -> test_execution -> exception -> finding ->
remediation -> re-test chain rather than inventing a parallel one.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-05

"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_PERMISSION = ("devices:manage", "Enroll and manage endpoint devices.")
_ROLES = ["Platform Admin", "Audit Manager", "IT/Audit Technical User", "Client IT Admin"]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE devices (
            device_id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id                UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
            device_name                    VARCHAR(150) NOT NULL,
            hostname                       VARCHAR(150),
            os_name                        VARCHAR(100),
            os_version                     VARCHAR(100),
            registration_code              VARCHAR(20),
            registration_code_expires_at   TIMESTAMPTZ,
            registration_status            VARCHAR(20) NOT NULL DEFAULT 'unused'
                                            CHECK (registration_status IN ('unused','registered','expired','revoked')),
            device_certificate_fingerprint VARCHAR(255),
            status                         VARCHAR(20) NOT NULL DEFAULT 'pending'
                                            CHECK (status IN ('pending','online','offline','deregistered')),
            agent_version                  VARCHAR(20),
            last_heartbeat                 TIMESTAMPTZ,
            created_by                     UUID REFERENCES users(user_id) ON DELETE SET NULL,
            created_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_devices_org ON devices(organization_id)")

    op.execute(
        """
        CREATE TABLE device_telemetry (
            telemetry_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            device_id               UUID NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
            collected_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            antivirus_enabled       BOOLEAN,
            firewall_enabled        BOOLEAN,
            disk_encryption_enabled BOOLEAN,
            os_up_to_date           BOOLEAN,
            raw_payload             JSONB
        )
        """
    )
    op.execute("CREATE INDEX idx_device_telemetry_device ON device_telemetry(device_id)")

    name, description = _PERMISSION
    op.execute(
        f"INSERT INTO permissions (permission_name, description) VALUES ('{name}', '{description}') "
        f"ON CONFLICT (permission_name) DO NOTHING"
    )
    for role_name in _ROLES:
        op.execute(
            "INSERT INTO role_permissions (role_id, permission_id) "
            f"SELECT r.role_id, p.permission_id FROM roles r, permissions p "
            f"WHERE r.role_name = '{role_name}' AND p.permission_name = '{name}' "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        )


def downgrade() -> None:
    name, _ = _PERMISSION
    op.execute(f"DELETE FROM role_permissions WHERE permission_id IN (SELECT permission_id FROM permissions WHERE permission_name = '{name}')")
    op.execute(f"DELETE FROM permissions WHERE permission_name = '{name}'")
    op.execute("DROP TABLE IF EXISTS device_telemetry")
    op.execute("DROP TABLE IF EXISTS devices")
