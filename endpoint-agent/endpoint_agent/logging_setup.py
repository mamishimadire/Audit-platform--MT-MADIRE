"""
Every entry point (GUI, CLI, service) calls configure() first. Nothing the
agent does should ever be able to "disappear" the way the bare console
window did — a failure during startup, registration, or a service cycle
always lands in logs/endpoint-agent.log, in addition to stdout when a
console is attached.
"""
import logging
import sys
import traceback
from pathlib import Path

if getattr(sys, "frozen", False):
    INSTALL_DIR = Path(sys.executable).resolve().parent
else:
    INSTALL_DIR = Path(__file__).resolve().parent.parent  # endpoint_agent/logging_setup.py -> endpoint-agent/
LOG_DIR = INSTALL_DIR / "logs"
LOG_PATH = LOG_DIR / "endpoint-agent.log"

_configured = False


def configure() -> None:
    global _configured
    if _configured:
        return
    LOG_DIR.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)

    def _log_uncaught(exc_type, exc_value, exc_tb):
        logging.getLogger("endpoint_agent").critical(
            "Unhandled exception — this is what used to vanish with the console window:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )

    sys.excepthook = _log_uncaught
    _configured = True
    logging.getLogger("endpoint_agent").info("Logging initialised — writing to %s", LOG_PATH)
