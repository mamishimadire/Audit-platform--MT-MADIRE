"""One-time enrollment: redeems a registration code for a device identity.

    python -m endpoint_agent.register --url https://platform.example.com/api/v1 --code AT7F-9K2D-4B6M
"""
import argparse
import logging
import platform
import sys
from pathlib import Path

from endpoint_agent import deployment_config, logging_setup
from endpoint_agent.client import PlatformClient
from endpoint_agent.identity import DeviceIdentity, save_identity
from endpoint_agent.telemetry import collect

# Bump this whenever the agent's actual reporting capability changes —
# it's the only signal the platform has for "this device is running a
# stale agent and needs reinstalling" (see Devices.tsx's outdated-agent hint).
# 0.2.0: adds software inventory collection, compliance-policy awareness,
# and remote command execution (run_check_now, restart).
AGENT_VERSION = "0.2.0"
PLATFORM_URL_PATH = Path("platform_url.txt")

logging_setup.configure()
logger = logging.getLogger("endpoint_agent.register")


def main() -> None:
    # A --windowed production build has sys.stdout/sys.stderr set to None —
    # this CLI subcommand path is for scripting/diagnostics (the GUI never
    # calls this), so every message here goes through the logger (which is
    # already None-safe), never a raw print() that would crash silently.
    parser = argparse.ArgumentParser(description="Enroll this device with the Madire Endpoint Agent platform")
    parser.add_argument("--url", default=deployment_config.PLATFORM_URL, help="Only needed to override the platform this build ships with")
    parser.add_argument("--code", required=True)
    parser.add_argument("--name", default=platform.node())
    args = parser.parse_args()

    snapshot = collect()
    client = PlatformClient(base_url=args.url)
    try:
        result = client.register(
            registration_code=args.code,
            device_name=args.name,
            hostname=snapshot["hostname"],
            os_name=snapshot["os_name"],
            os_version=snapshot["os_version"],
            agent_version=AGENT_VERSION,
        )
    except Exception as exc:
        logger.error("Registration failed: %s", exc)
        sys.exit(1)

    identity = DeviceIdentity(device_id=result["device_id"], organization_id=result["organization_id"], api_key=result["api_key"])
    save_identity(identity)
    PLATFORM_URL_PATH.write_text(args.url.strip())
    logger.info("Registered. Device identity saved — keep it out of version control.")


if __name__ == "__main__":
    main()
