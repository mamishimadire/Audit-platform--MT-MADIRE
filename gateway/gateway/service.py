"""
Runs the Gateway as a real Windows Service (Services.msc / `sc query`), so
it behaves like any other installed Windows background application instead
of a script someone has to remember to re-run — starts on boot, keeps
running after logoff, restartable/removable only by an administrator.

Install (run once, from an elevated/Administrator prompt, from the folder
the exe lives in — it always operates relative to its own directory so this
works no matter what the service's start-up working directory is):

    MadireGateway.exe service install
    MadireGateway.exe service start

Manage:
    MadireGateway.exe service stop
    MadireGateway.exe service remove
"""
import logging
import sys
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil

from gateway import logging_setup
from gateway.client import PlatformClient
from gateway.config import load_settings
from gateway.identity import load_identity
from gateway.main import run_once
from gateway.register import GATEWAY_VERSION

INSTALL_DIR = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent

logging_setup.configure()
logger = logging.getLogger("gateway.service")


class MadireGatewayService(win32serviceutil.ServiceFramework):
    _svc_name_ = "MadireGateway"
    _svc_display_name_ = "Madire Audit Data Gateway"
    _svc_description_ = (
        "Connects this network's databases to the Madire Audit Platform for continuous audit testing. "
        "Outbound-only — never accepts inbound connections."
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
        self._run()

    def _run(self):
        # A Windows Service's working directory defaults to System32 —
        # config.yaml and .gateway_identity.json live next to the exe.
        # Read here, inside SvcDoRun, not in __init__ — a missing/not-yet-
        # configured file must be a clearly logged startup failure, not an
        # opaque exception during pywin32's own object construction.
        try:
            config_path = INSTALL_DIR / "config.yaml"
            identity = load_identity(INSTALL_DIR / ".gateway_identity.json")
            settings = load_settings(config_path)
        except Exception:
            logger.exception("Cannot start: not registered and/or config.yaml missing — run the setup app first")
            servicemanager.LogErrorMsg(f"{self._svc_name_}: not configured — run MadireGateway.exe to register and set up config.yaml first")
            self.ReportServiceStatus(win32service.SERVICE_STOPPED)
            return
        client = PlatformClient(base_url=settings.platform_url, identity=identity)

        while self.running:
            try:
                run_once(settings, client)
            except Exception:
                logger.exception("Gateway cycle failed; will retry next interval")
                servicemanager.LogErrorMsg(f"{self._svc_name_}: gateway cycle failed, will retry")

            # Wake early if a stop was requested, otherwise wait one interval.
            rc = win32event.WaitForSingleObject(self.stop_event, settings.heartbeat_interval_seconds * 1000)
            if rc == win32event.WAIT_OBJECT_0:
                break


def main() -> None:
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(MadireGatewayService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(MadireGatewayService)


if __name__ == "__main__":
    main()
