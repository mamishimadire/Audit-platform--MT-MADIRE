"""
Runs one telemetry collect-and-report cycle (or loops on an interval).
This is the same pattern as gateway/gateway/main.py: outbound-only calls,
initiated on the device's own schedule.

    python -m endpoint_agent.main                 # one report
    python -m endpoint_agent.main --loop --interval 300
"""
import argparse
import logging
import time

from endpoint_agent import commands, logging_setup
from endpoint_agent.client import PlatformClient
from endpoint_agent.identity import load_identity
from endpoint_agent.register import AGENT_VERSION, PLATFORM_URL_PATH
from endpoint_agent.telemetry import collect

logging_setup.configure()
logger = logging.getLogger("endpoint_agent")


def run_once(client: PlatformClient) -> None:
    report = collect()
    report["agent_version"] = AGENT_VERSION
    client.report_telemetry(report)
    logger.info("Telemetry reported: %s", {k: v for k, v in report.items() if k != "raw_payload"})
    poll_commands(client)


def poll_commands(client: PlatformClient) -> None:
    """
    Checking for admin-queued commands is cheap (one small GET) — the full
    telemetry cycle isn't (PowerShell calls plus a registry scan for every
    installed application). Splitting them lets a queued restart/check-now
    get picked up in seconds rather than waiting for the next multi-minute
    telemetry interval; see service.py for how the two are scheduled
    independently.
    """
    try:
        instructions = client.get_instructions()
    except Exception:
        logger.debug("Could not fetch instructions — will retry shortly")
        return

    for pending in instructions.get("pending_commands", []):
        command_id = pending["command_id"]
        command_type = pending["command_type"]
        logger.info("Executing command: %s", command_type)
        success, message = commands.execute(command_type, client)
        try:
            client.report_command_result(command_id, success=success, message=message)
        except Exception:
            logger.exception("Could not report result for command %s", command_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Madire Endpoint Agent")
    parser.add_argument("--url", default=None, help="Platform API base URL (defaults to the URL saved at registration)")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between reports when looping")
    args = parser.parse_args()

    url = args.url or (PLATFORM_URL_PATH.read_text().strip() if PLATFORM_URL_PATH.exists() else None)
    if not url:
        raise SystemExit("No platform URL known — pass --url or run `register` first.")

    identity = load_identity()
    client = PlatformClient(base_url=url, identity=identity)

    run_once(client)
    if not args.loop:
        return

    while True:
        time.sleep(args.interval)
        try:
            run_once(client)
        except Exception:
            logger.exception("Telemetry cycle failed; will retry next interval")


if __name__ == "__main__":
    main()
