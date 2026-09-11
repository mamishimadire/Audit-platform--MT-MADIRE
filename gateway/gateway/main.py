"""
Gateway runtime: for each configured connection, test connectivity and run
discovery; then pull any audit tests that are due and execute them locally
against the client's own database, reporting results back. Runs once (good
for cron/Task Scheduler) or loops (`--loop`) for a long-running process —
V1 deliberately keeps this simple rather than requiring a message broker or
job queue, since the Gateway always initiates its own outbound calls on its
own schedule.

Usage:
    python -m gateway.main --config config.yaml            # one pass
    python -m gateway.main --config config.yaml --loop      # keep running
"""
import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from gateway import logging_setup, rule_engine
from gateway.client import PlatformClient
from gateway.config import ConnectionEntry, GatewaySettings, load_settings
from gateway.connectors import build_connector
from gateway.identity import load_identity
from gateway.register import GATEWAY_VERSION

logging_setup.configure()
logger = logging.getLogger("gateway")


def run_once(settings: GatewaySettings, client: PlatformClient) -> None:
    connection_by_id: dict[str, ConnectionEntry] = {c.connection_id: c for c in settings.connections}

    for entry in settings.connections:
        connector = build_connector(entry.source_type, entry.connector_config)
        try:
            success, detail = connector.test_connection()
            client.report_test_result(entry.connection_id, success=success, detail=detail)
            if not success:
                logger.warning("Connection %s failed: %s", entry.connection_id, detail)
                continue
            logger.info("Connection %s OK — running discovery", entry.connection_id)

            entities = connector.discover()
            client.report_discovery(entry.connection_id, entities)
            logger.info("Connection %s: reported %d entities", entry.connection_id, len(entities))
        finally:
            connector.close()

    run_due_tests(settings, client, connection_by_id)

    client.heartbeat(version=GATEWAY_VERSION)
    logger.info("Heartbeat sent")


def run_due_tests(settings: GatewaySettings, client: PlatformClient, connection_by_id: dict[str, ConnectionEntry]) -> None:
    try:
        due_tests = client.get_due_tests()
    except Exception:
        logger.exception("Could not fetch due tests")
        return

    for due in due_tests:
        started_at = datetime.now(timezone.utc)
        try:
            dataframes = {}
            for canonical_object, obj in due["objects"].items():
                connection_id = obj["connection_id"]
                entry = connection_by_id.get(connection_id)
                if entry is None:
                    raise RuntimeError(f"Connection {connection_id} is not configured locally in config.yaml")
                connector = build_connector(entry.source_type, entry.connector_config)
                try:
                    physical_columns = list(obj["fields"].values())
                    df = connector.fetch_dataframe(obj["entity_name"], physical_columns)
                    df = df.rename(columns={physical: canonical for canonical, physical in obj["fields"].items()})
                    dataframes[canonical_object] = df
                finally:
                    connector.close()

            result = rule_engine.evaluate(due["rule_definition"], dataframes)
            completed_at = datetime.now(timezone.utc)
            client.report_execution(
                {
                    "audit_test_id": due["audit_test_id"],
                    "schedule_id": due["schedule_id"],
                    "started_at": started_at.isoformat(),
                    "completed_at": completed_at.isoformat(),
                    "status": "completed",
                    "records_analyzed": result.records_analyzed,
                    "exceptions": result.exceptions,
                }
            )
            logger.info(
                "Executed audit test %s: %d records analyzed, %d exceptions",
                due["audit_test_id"],
                result.records_analyzed,
                len(result.exceptions),
            )
        except Exception as exc:  # noqa: BLE001 — a failed test must be reported as FAILED, never silently dropped
            completed_at = datetime.now(timezone.utc)
            logger.exception("Audit test %s failed", due["audit_test_id"])
            try:
                client.report_execution(
                    {
                        "audit_test_id": due["audit_test_id"],
                        "schedule_id": due["schedule_id"],
                        "started_at": started_at.isoformat(),
                        "completed_at": completed_at.isoformat(),
                        "status": "failed",
                        "error_message": str(exc),
                    }
                )
            except Exception:
                logger.exception("Could not even report the failure for audit test %s", due["audit_test_id"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Audit Data Gateway")
    parser.add_argument("--config", default="config.yaml", type=Path)
    parser.add_argument("--loop", action="store_true", help="Keep running, re-testing/discovering/executing on the configured interval")
    args = parser.parse_args()

    identity = load_identity()
    settings = load_settings(args.config)
    client = PlatformClient(base_url=settings.platform_url, identity=identity)

    run_once(settings, client)
    if not args.loop:
        return

    while True:
        time.sleep(settings.heartbeat_interval_seconds)
        try:
            run_once(settings, client)
        except Exception:  # noqa: BLE001 — a bad cycle must not kill the loop
            logger.exception("Gateway cycle failed; will retry next interval")


if __name__ == "__main__":
    main()
