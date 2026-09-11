"""
Thin HTTP client for the Gateway's outbound-only calls to the cloud
platform. The Gateway always initiates — the platform never opens a
connection into the client's network (product spec, Section 6/10).
"""
import requests

from gateway.identity import GatewayIdentity


class PlatformClient:
    def __init__(self, base_url: str, identity: GatewayIdentity | None = None, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.identity = identity
        self.timeout = timeout

    def register(self, registration_code: str, gateway_name: str | None, version: str) -> dict:
        resp = requests.post(
            f"{self.base_url}/gateways/register",
            json={"registration_code": registration_code, "gateway_name": gateway_name, "version": version},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _auth_headers(self) -> dict[str, str]:
        if self.identity is None:
            raise RuntimeError("PlatformClient has no identity — call register() or load one first")
        return self.identity.auth_headers()

    def heartbeat(self, version: str) -> dict:
        resp = requests.post(
            f"{self.base_url}/gateways/{self.identity.gateway_id}/heartbeat",
            json={"version": version},
            headers=self._auth_headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def report_test_result(self, connection_id: str, success: bool, detail: str | None) -> dict:
        resp = requests.post(
            f"{self.base_url}/gateways/{self.identity.gateway_id}/connections/{connection_id}/test-result",
            json={"success": success, "detail": detail},
            headers=self._auth_headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def get_due_tests(self) -> list[dict]:
        resp = requests.get(
            f"{self.base_url}/gateways/{self.identity.gateway_id}/due-tests", headers=self._auth_headers(), timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()

    def report_execution(self, report: dict) -> dict:
        resp = requests.post(
            f"{self.base_url}/gateways/{self.identity.gateway_id}/execution-reports",
            json=report,
            headers=self._auth_headers(),
            timeout=self.timeout * 4,
        )
        resp.raise_for_status()
        return resp.json()

    def report_discovery(self, connection_id: str, entities: list[dict]) -> dict:
        resp = requests.post(
            f"{self.base_url}/gateways/{self.identity.gateway_id}/connections/{connection_id}/discovery",
            json={"entities": entities},
            headers=self._auth_headers(),
            timeout=self.timeout * 20,  # discovery does one write per table server-side; a first large run can be slow
        )
        resp.raise_for_status()
        return resp.json()
