"""
Single entry point for the packaged MadireEndpointAgent.exe — mirrors
gateway/gateway/cli.py. Always operates relative to its own install
directory, not the caller's working directory.

Double-clicked with no arguments (the normal, traditional-app way to launch
it): opens a setup window instead of printing usage text and exiting —
that "print and exit in milliseconds" behaviour was the entire cause of the
console flashing open and closing immediately.

    MadireEndpointAgent.exe                                  (opens the setup window)
    MadireEndpointAgent.exe register --url <platform-url> --code <code>
    MadireEndpointAgent.exe run
    MadireEndpointAgent.exe service install|start|stop|remove
"""
import os
import sys
from pathlib import Path

INSTALL_DIR = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
os.chdir(INSTALL_DIR)

from endpoint_agent import logging_setup  # noqa: E402 — after chdir, so logs/ lands next to the exe

logging_setup.configure()


def main() -> None:
    if len(sys.argv) < 2:
        # Two completely different things invoke us with zero extra
        # arguments: a user double-clicking the exe, and Windows' Service
        # Control Manager starting the installed service. Try the SCM
        # dispatcher first — it fails fast (not silently) when we weren't
        # actually launched by SCM, which is exactly the signal to fall
        # back to opening the setup window instead.
        try:
            import servicemanager
            from endpoint_agent.service import MadireEndpointAgentService

            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(MadireEndpointAgentService)
            servicemanager.StartServiceCtrlDispatcher()
        except Exception:
            from endpoint_agent.gui import main as gui_main

            gui_main()
        return

    command, rest = sys.argv[1], sys.argv[2:]

    if command == "register":
        from endpoint_agent import register

        sys.argv = [sys.argv[0], *rest]
        register.main()
    elif command == "run":
        from endpoint_agent import main as agent_main

        sys.argv = [sys.argv[0], *rest]
        agent_main.main()
    elif command == "service":
        from endpoint_agent.service import MadireEndpointAgentService
        import win32serviceutil

        win32serviceutil.HandleCommandLine(MadireEndpointAgentService, argv=[sys.argv[0], *rest])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
