"""
Runs the Endpoint Agent as a protected Windows Service — SYSTEM-level, so
it can read BitLocker/security state a normal user session can't (verified
during development: `manage-bde -status` needs elevation), starts on boot,
and isn't something a standard employee account can simply stop or
uninstall. Mirrors gateway/gateway/service.py exactly.

    python -m endpoint_agent.service install
    python -m endpoint_agent.service start
"""
import logging
import sys
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil

from endpoint_agent import logging_setup
from endpoint_agent.client import PlatformClient
from endpoint_agent.identity import load_identity
from endpoint_agent.main import poll_commands, run_once

# BUG FIXED HERE: this used to be `.parent.parent`. For a frozen exe,
# sys.executable IS the exe file itself, so a single `.parent` is its
# directory (where platform_url.txt / .device_identity.json actually live,
# same convention as cli.py's INSTALL_DIR) — the extra `.parent` pointed one
# directory too high and would have made the installed service unable to
# find its own identity/config file at all.
INSTALL_DIR = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
if not getattr(sys, "frozen", False):
    INSTALL_DIR = INSTALL_DIR.parent  # dev mode: endpoint_agent/service.py -> repo root

logging_setup.configure()
logger = logging.getLogger("endpoint_agent.service")


class MadireEndpointAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "MadireEndpointAgent"
    _svc_display_name_ = "Madire Endpoint Agent"
    _svc_description_ = (
        "Reports this device's security status (antivirus, firewall, disk encryption, patch level) to the "
        "Madire Audit Platform for continuous endpoint compliance testing. Does not accept inbound connections."
    )

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.running = True

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.running = False
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE, servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ""))
        # Deliberately read config/identity here, inside SvcDoRun, not in
        # __init__ — a missing/unregistered-yet file must be a clearly
        # logged startup failure, not an opaque exception during pywin32's
        # own object construction (which would be far harder to diagnose).
        try:
            platform_url = (INSTALL_DIR / "platform_url.txt").read_text().strip()
            identity = load_identity(INSTALL_DIR / ".device_identity.json")
        except Exception:
            logger.exception("Cannot start: this device hasn't been registered yet (run the setup app first)")
            servicemanager.LogErrorMsg(f"{self._svc_name_}: not registered — run MadireEndpointAgent.exe to register first")
            self.ReportServiceStatus(win32service.SERVICE_STOPPED)
            return
        client = PlatformClient(base_url=platform_url, identity=identity)
        # Two independent cadences: the full telemetry cycle (PowerShell
        # calls + a registry scan of every installed app) is genuinely
        # expensive, so it stays infrequent — but an admin-queued command
        # like "restart" or "run check now" should land in seconds, not
        # wait for that same multi-minute interval. COMMAND_POLL_SECONDS
        # governs the wait granularity; TELEMETRY_INTERVAL_SECONDS is a
        # multiple of it.
        COMMAND_POLL_SECONDS = 10
        TELEMETRY_INTERVAL_SECONDS = 300
        seconds_until_next_report = 0  # run a full report immediately on startup

        while self.running:
            if seconds_until_next_report <= 0:
                try:
                    run_once(client)
                except Exception:
                    logger.exception("Telemetry cycle failed; will retry next interval")
                    servicemanager.LogErrorMsg(f"{self._svc_name_}: telemetry cycle failed, will retry")
                seconds_until_next_report = TELEMETRY_INTERVAL_SECONDS
            else:
                try:
                    poll_commands(client)
                except Exception:
                    logger.exception("Command poll failed; will retry shortly")

            rc = win32event.WaitForSingleObject(self.stop_event, COMMAND_POLL_SECONDS * 1000)
            if rc == win32event.WAIT_OBJECT_0:
                break
            seconds_until_next_report -= COMMAND_POLL_SECONDS


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(MadireEndpointAgentService)
