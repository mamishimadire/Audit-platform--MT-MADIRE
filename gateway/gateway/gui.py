"""
The double-click entry point. Replaces "print usage, exit immediately" with
an actual window — mirrors endpoint_agent/gui.py. Registration only needs a
URL and code; the database connection itself still needs config.yaml
(host/port/credentials), which this window creates from the example file
and opens in the user's own text editor rather than reinventing a form for
every possible database engine.
"""
import logging
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

from gateway import deployment_config, logging_setup
from gateway.client import PlatformClient
from gateway.identity import DEFAULT_IDENTITY_PATH, GatewayIdentity, save_identity
from gateway.register import GATEWAY_VERSION

logger = logging.getLogger("gateway.gui")

INSTALL_DIR = logging_setup.INSTALL_DIR
CONFIG_PATH = INSTALL_DIR / "config.yaml"
CONFIG_EXAMPLE_PATH = INSTALL_DIR / "config.example.yaml"
PLATFORM_URL_PATH = INSTALL_DIR / "platform_url.txt"

_SELF = sys.executable if getattr(sys, "frozen", False) else [sys.executable, "-m", "gateway.cli"]


def _run_self_elevated(*args: str, timeout: int = 60) -> tuple[bool, str]:
    """
    Elevates ONLY this one subprocess (installing/starting the Windows
    Service) via a UAC prompt — mirrors endpoint_agent/gui.py exactly. The
    setup window itself requires no elevation at all, so double-clicking
    the app to register never triggers UAC and can never hang waiting on a
    secure-desktop prompt that failed to render (the cause of a device
    freezing at launch when the whole exe required admin just to open).

    Runs on a background thread (see _on_install) — ShellExecuteEx is a
    COM-based API, and calling it from a thread that never initialized COM
    is a known source of spurious failures (a bogus "canceled by the user"
    when nothing was canceled) and leftover window/focus glitches in the
    caller's UI afterward. CoInitialize/CoUninitialize around the one call
    this makes keeps the thread's COM apartment properly set up.
    """
    import pythoncom
    from win32com.shell import shell, shellcon
    import win32event
    import win32process

    exe = _SELF if isinstance(_SELF, str) else _SELF[0]
    lead_args = [] if isinstance(_SELF, str) else _SELF[1:]
    param_str = subprocess.list2cmdline(lead_args + list(args))

    pythoncom.CoInitialize()
    try:
        proc_info = shell.ShellExecuteEx(
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,
            lpVerb="runas",
            lpFile=exe,
            lpParameters=param_str,
            nShow=0,
        )
        handle = proc_info["hProcess"]
        wait_result = win32event.WaitForSingleObject(handle, timeout * 1000)
        if wait_result != win32event.WAIT_OBJECT_0:
            return False, "Timed out waiting for the elevated step to finish."
        exit_code = win32process.GetExitCodeProcess(handle)
        if exit_code == 0:
            return True, "Done."
        return False, f"Exited with code {exit_code} — see logs/gateway.log for details."
    except Exception as exc:
        return False, f"Elevation was cancelled or failed: {exc}"
    finally:
        pythoncom.CoUninitialize()


class SetupWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Madire Audit Data Gateway — Setup")
        root.geometry("600x460")
        root.resizable(False, False)

        already_registered = DEFAULT_IDENTITY_PATH.exists()

        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Madire Audit Data Gateway", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Connects your database(s) to the Madire Audit Platform — outbound only", foreground="#555").pack(
            anchor="w", pady=(0, 12)
        )

        # Pre-filled with the platform baked into this build — every Gateway
        # points at the same one platform, only the registration code is
        # device-specific. Left editable for the rare case of pointing a
        # dev build at a different environment.
        saved_url = PLATFORM_URL_PATH.read_text().strip() if PLATFORM_URL_PATH.exists() else deployment_config.PLATFORM_URL

        form = ttk.Frame(frame)
        form.pack(fill="x")
        ttk.Label(form, text="Platform URL").grid(row=0, column=0, sticky="w", pady=2)
        self.url_entry = ttk.Entry(form, width=50)
        self.url_entry.insert(0, saved_url)
        self.url_entry.grid(row=0, column=1, pady=2)

        ttk.Label(form, text="Registration code").grid(row=1, column=0, sticky="w", pady=2)
        self.code_entry = ttk.Entry(form, width=50)
        self.code_entry.grid(row=1, column=1, pady=2)
        self.code_entry.focus_set()

        self.status_var = tk.StringVar(value="Already registered on this machine." if already_registered else "Not yet registered.")
        ttk.Label(frame, textvariable=self.status_var, foreground="#2a6").pack(anchor="w", pady=(10, 4))

        button_row = ttk.Frame(frame)
        button_row.pack(anchor="w", pady=(0, 4))
        self.register_button = ttk.Button(button_row, text="Register", command=self._on_register)
        self.register_button.pack(side="left")
        self.edit_config_button = ttk.Button(button_row, text="Edit config.yaml", command=self._on_edit_config, state="disabled")
        self.edit_config_button.pack(side="left", padx=6)
        self.install_button = ttk.Button(button_row, text="Install && Start Service", command=self._on_install, state="disabled")
        self.install_button.pack(side="left")

        if already_registered:
            self.edit_config_button.config(state="normal")
            self.install_button.config(state="normal" if CONFIG_PATH.exists() else "disabled")

        ttk.Label(frame, text="Log", foreground="#555").pack(anchor="w", pady=(12, 2))
        self.log_box = scrolledtext.ScrolledText(frame, height=13, width=72, state="disabled", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True)

        self._log(f"Madire Gateway v{GATEWAY_VERSION}. Logs also written to logs/gateway.log.")
        if already_registered and not CONFIG_PATH.exists():
            self._log("Registered, but config.yaml is missing — click 'Edit config.yaml' to create and fill it in.")

    def _log(self, message: str) -> None:
        self.log_box.config(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")
        logger.info(message)

    def _on_register(self) -> None:
        url = self.url_entry.get().strip()
        code = self.code_entry.get().strip()
        if not url or not code:
            self._log("Enter both the platform URL and the registration code.")
            return
        self.register_button.config(state="disabled")
        threading.Thread(target=self._register_worker, args=(url, code), daemon=True).start()

    def _register_worker(self, url: str, code: str) -> None:
        try:
            self.root.after(0, self._log, f"Registering with {url} …")
            client = PlatformClient(base_url=url)
            result = client.register(registration_code=code, gateway_name=None, version=GATEWAY_VERSION)
            identity = GatewayIdentity(
                gateway_id=result["gateway_id"], organization_id=result["organization_id"], api_key=result["api_key"]
            )
            save_identity(identity)
            self.root.after(0, self._log, "Registered. Gateway identity saved.")
        except Exception as exc:
            logger.exception("Registration failed")
            self.root.after(0, self._log, f"Registration failed: {exc}")
            self.root.after(0, lambda: self.register_button.config(state="normal"))
            return

        if not CONFIG_PATH.exists() and CONFIG_EXAMPLE_PATH.exists():
            text = CONFIG_EXAMPLE_PATH.read_text()
            text = text.replace('platform_url: "https://your-platform.example.com/api/v1"', f'platform_url: "{url}"')
            CONFIG_PATH.write_text(text)
            self.root.after(0, self._log, "Created config.yaml with your platform URL pre-filled.")

        self.root.after(0, self._log, "Now click 'Edit config.yaml' to add your database connection details.")
        self.root.after(0, lambda: self.edit_config_button.config(state="normal"))
        self.root.after(0, lambda: self.install_button.config(state="normal"))
        self.root.after(0, lambda: self.register_button.config(state="normal", text="Re-register"))

    def _on_edit_config(self) -> None:
        if not CONFIG_PATH.exists() and CONFIG_EXAMPLE_PATH.exists():
            CONFIG_PATH.write_text(CONFIG_EXAMPLE_PATH.read_text())
        try:
            os.startfile(str(CONFIG_PATH))  # opens in the user's default text editor — traditional Windows behaviour
            self._log(f"Opened {CONFIG_PATH.name} — fill in your database host/port/credentials, save, then close it.")
        except OSError as exc:
            self._log(f"Could not open config.yaml automatically: {exc}. Edit it manually at {CONFIG_PATH}.")

    def _on_install(self) -> None:
        self.install_button.config(state="disabled")
        self.status_var.set("Working…")
        threading.Thread(target=self._install_worker, daemon=True).start()

    def _install_worker(self) -> None:
        self.root.after(0, self._log, "Installing Windows Service — a Windows permission prompt will appear, click Yes…")
        # --startup auto: without it pywin32 defaults to Manual start, so a
        # reboot of the host machine silently stops the Gateway (and every
        # audit test it runs) until someone logs in and starts it by hand.
        # pywin32's arg parser stops scanning for flags at the first
        # non-flag token — "--startup auto" must come BEFORE "install" or
        # it's silently swallowed as a positional arg, printing usage() and
        # exiting 1 instead of installing. Verified directly against
        # endpoint-agent's identical service.py CLI.
        install_ok, install_msg = _run_self_elevated("service", "--startup", "auto", "install")
        self.root.after(0, self._log, install_msg)
        if not install_ok:
            self.root.after(0, self._on_worker_done, False)
            return

        self.root.after(0, self._log, "Starting service — another permission prompt will appear, click Yes…")
        start_ok, start_msg = _run_self_elevated("service", "start")
        self.root.after(0, self._log, start_msg)
        if not start_ok:
            self.root.after(0, self._on_worker_done, False)
            return

        self.root.after(0, self._log, "Service started — running in the background as 'Madire Audit Data Gateway'.")
        self.root.after(0, self._on_worker_done, True)

    def _on_worker_done(self, success: bool) -> None:
        self.install_button.config(state="normal", text="Reinstall && restart service")
        self.status_var.set("Running." if success else "Service step failed — see log.")


def main() -> None:
    logging_setup.configure()
    root = tk.Tk()
    SetupWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
