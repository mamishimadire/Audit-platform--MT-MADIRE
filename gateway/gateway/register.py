"""
One-time install step: redeems a registration code for a long-lived device
identity. Usage:

    python -m gateway.register --url https://platform.example.com/api/v1 \
        --code AT7F-9K2D-4B6M --name "Finance DB Gateway"
"""
import argparse
import logging
import platform
import sys

from gateway import deployment_config, logging_setup
from gateway.client import PlatformClient
from gateway.identity import DEFAULT_IDENTITY_PATH, GatewayIdentity, save_identity

GATEWAY_VERSION = "0.1.0"

logging_setup.configure()
logger = logging.getLogger("gateway.register")


def main() -> None:
    # A --windowed production build has sys.stdout/sys.stderr set to None —
    # this CLI subcommand path is for scripting/diagnostics (the GUI never
    # calls this), so every message here goes through the logger (which is
    # already None-safe), never a raw print() that would crash silently.
    parser = argparse.ArgumentParser(description="Register this Gateway with the platform")
    parser.add_argument("--url", default=deployment_config.PLATFORM_URL, help="Only needed to override the platform this build ships with")
    parser.add_argument("--code", required=True, help="Registration code from the Gateways screen")
    parser.add_argument("--name", default=platform.node(), help="Gateway name (defaults to this machine's hostname)")
    args = parser.parse_args()

    client = PlatformClient(base_url=args.url)
    try:
        result = client.register(registration_code=args.code, gateway_name=args.name, version=GATEWAY_VERSION)
    except Exception as exc:
        logger.error("Registration failed: %s", exc)
        sys.exit(1)

    identity = GatewayIdentity(
        gateway_id=result["gateway_id"], organization_id=result["organization_id"], api_key=result["api_key"]
    )
    save_identity(identity)
    logger.info("Registered. Identity saved to %s", DEFAULT_IDENTITY_PATH.resolve())
    logger.info("This file is the Gateway's device credential — keep it out of version control.")


if __name__ == "__main__":
    main()
