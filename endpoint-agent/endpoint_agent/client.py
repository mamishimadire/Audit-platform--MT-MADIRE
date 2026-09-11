"""Minimal HTTP client for the platform's device endpoints — outbound only,
same pattern as gateway/gateway/client.py."""
import requests

from endpoint_agent.identity import DeviceIdentity


class PlatformClient:
    def __init__(self, base_url: str, identity: DeviceIdentity | None = None):
        self.base_url = base_url.rstrip("/")
        self.identity = identity

    def register(self, *, registration_code: str, device_name: str | None, hostname: str, os_name: str, os_version: str, agent_version: str) -> dict:
        resp = requests.post(
            f"{self.base_url}/devices/register",
            json={
                "registration_code": registration_code,
                "device_name": device_name,
                "hostname": hostname,
                "os_name": os_name,
                "os_version": os_version,
                "agent_version": agent_version,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def report_telemetry(self, report: dict) -> dict:
        assert self.identity is not None
        resp = requests.post(
            f"{self.base_url}/devices/{self.identity.device_id}/telemetry",
            json=report,
            headers=self.identity.auth_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_instructions(self) -> dict:
        """Polled right after each telemetry report — current compliance
        policy plus any admin-queued commands awaiting execution."""
        assert self.identity is not None
        resp = requests.get(
            f"{self.base_url}/devices/{self.identity.device_id}/instructions",
            headers=self.identity.auth_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def report_command_result(self, command_id: str, *, success: bool, message: str | None) -> dict:
        assert self.identity is not None
        resp = requests.post(
            f"{self.base_url}/devices/{self.identity.device_id}/commands/{command_id}/result",
            json={"success": success, "message": message},
            headers=self.identity.auth_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
