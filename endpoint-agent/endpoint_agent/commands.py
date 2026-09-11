"""
Executes admin-queued commands the platform hands back via
GET /devices/{id}/instructions. Fixed allowlist only — an unrecognized
command_type is reported as failed, never attempted; this is the one place
in the agent that takes an instruction from the platform and turns it into
an actual system action, so it stays deliberately small and explicit.
"""
import logging
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endpoint_agent.client import PlatformClient

logger = logging.getLogger("endpoint_agent.commands")


def _run_check_now(client: "PlatformClient") -> tuple[bool, str]:
    # A command is only ever seen on the poll right after a normal telemetry
    # report — so "now" means immediately re-running collection and sending
    # a second report right here, rather than waiting for the next interval
    # (which is exactly the point of an admin reaching for this button).
    from endpoint_agent.register import AGENT_VERSION
    from endpoint_agent.telemetry import collect

    report = collect()
    report["agent_version"] = AGENT_VERSION
    client.report_telemetry(report)
    return True, "Re-ran the security & inventory check"


def _restart(_client: "PlatformClient") -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["shutdown", "/r", "/t", "30", "/c", "Restart requested from the Madire Audit Platform"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return True, "Restart scheduled in 30 seconds"
        return False, f"shutdown.exe returned code {result.returncode}: {result.stderr.strip()}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Could not schedule restart: {exc}"


_HANDLERS = {
    "run_check_now": _run_check_now,
    "restart": _restart,
}


def execute(command_type: str, client: "PlatformClient") -> tuple[bool, str]:
    handler = _HANDLERS.get(command_type)
    if handler is None:
        return False, f"Unknown command type: {command_type}"
    try:
        return handler(client)
    except Exception as exc:  # noqa: BLE001 — a command failure must never crash the agent's main loop
        logger.exception("Command %s failed", command_type)
        return False, str(exc)
