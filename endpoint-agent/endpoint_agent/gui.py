"""
The double-click entry point. Replaces "print usage, exit immediately" with
an actual window — this is the fix for the flash-and-close behaviour.
Register + install-as-service + start, all from one screen, with a visible
log instead of a console that vanishes on exit.
"""
import logging
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

from endpoint_agent import deployment_config, logging_setup
from endpoint_agent.client import PlatformClient
from endpoint_agent.identity import DeviceIdentity, DEFAULT_IDENTITY_PATH, save_identity
from endpoint_agent.register import AGENT_VERSION, PLATFORM_URL_PATH
from endpoint_agent.telemetry import collect

_SELF = sys.executable if getattr(sys, "frozen", False) else [sys.executable, "-m", "endpoint_agent.cli"]

logger = logging.getLogger("endpoint_agent.gui")


def _run_self_elevated(*args: str, timeout: int = 60) -> tuple[bool, str]:
    """
    Elevates ONLY this one subprocess (installing/starting the Windows
    Service) via a UAC prompt — the setup window itself is built and run
    with no elevation requirement at all, so double-clicking the app to
    just enter a registration code never triggers UAC, and can never hang
    waiting on a secure-desktop prompt that failed to render (the likely
    cause of a device freezing at launch when the whole exe required admin
    just to open its own window).

    ShellExecute's "runas" verb launches a genuinely separate elevated
    process — there's no stdout/stderr pipe back to us the way a normal
    subprocess.run() gives you, so success is judged by exit code, and
    details go to logs/endpoint-agent.log (already written by service.py)
    rather than being echoed here.

    This runs on a background thread (see _on_install), and ShellExecuteEx
    is a COM-based API — calling it from a thread that never initialized
    COM is a known source of spurious failures (including a bogus "canceled
    by the user" when nothing was actually canceled) and of leftover
    window/focus glitches in the caller's UI after the secure-desktop
    transition, which is exactly what turned up in testing. CoInitialize
    on entry and CoUninitialize on exit keeps this thread's COM apartment
    properly set up for the one call it makes.
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
            nShow=0,  # SW_HIDE — a pywin32 console flash would look broken here
        )
        handle = proc_info["hProcess"]
        wait_result = win32event.WaitForSingleObject(handle, timeout * 1000)
        if wait_result != win32event.WAIT_OBJECT_0:
            return False, "Timed out waiting for the elevated step to finish."
        exit_code = win32process.GetExitCodeProcess(handle)
        if exit_code == 0:
            return True, "Done."
        return False, f"Exited with code {exit_code} — see logs/endpoint-agent.log for details."
    except Exception as exc:
        # A user genuinely clicking "No" on the UAC prompt also raises here —
        # that's a normal outcome, not a bug, so it's reported plainly.
        return False, f"Elevation was cancelled or failed: {exc}"
    finally:
        pythoncom.CoUninitialize()


class SetupWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Madire Endpoint Agent — Setup")
        root.geometry("560x420")
        root.resizable(False, False)

        already_registered = DEFAULT_IDENTITY_PATH.exists()

        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Madire Endpoint Agent", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Managed corporate device — continuous audit & security monitoring", foreground="#555").pack(
            anchor="w", pady=(0, 12)
        )

        # The platform URL ships baked into the app (deployment_config.py) —
        # every device points at the same one platform, only the
        # registration code is device-specific, so this is pre-filled and
        # normally never needs touching. Left editable (not read-only) for
        # the rare case it does — e.g. pointing a dev build at a different
        # environment — rather than forcing a rebuild for that.
        saved_url = PLATFORM_URL_PATH.read_text().strip() if PLATFORM_URL_PATH.exists() else deployment_config.PLATFORM_URL

        form = ttk.Frame(frame)
        form.pack(fill="x")
        ttk.Label(form, text="Platform").grid(row=0, column=0, sticky="w", pady=2)
        self.url_entry = ttk.Entry(form, width=48)
        self.url_entry.insert(0, saved_url)
        self.url_entry.grid(row=0, column=1, pady=2)

        ttk.Label(form, text="Registration code").grid(row=1, column=0, sticky="w", pady=2)
        self.code_entry = ttk.Entry(form, width=48)
        self.code_entry.grid(row=1, column=1, pady=2)
        self.code_entry.focus_set()

        self.status_var = tk.StringVar(value="Already enrolled on this device." if already_registered else "Not yet enrolled.")
        ttk.Label(frame, textvariable=self.status_var, foreground="#2a6").pack(anchor="w", pady=(10, 4))

        self.install_button = ttk.Button(frame, text="Register && Install Service", command=self._on_install)
        self.install_button.pack(anchor="w")
        if already_registered:
            self.install_button.config(text="Re-register && reinstall service")

        ttk.Label(frame, text="Log", foreground="#555").pack(anchor="w", pady=(12, 2))
        self.log_box = scrolledtext.ScrolledText(frame, height=12, width=68, state="disabled", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True)

        self._log(f"Madire Endpoint Agent v{AGENT_VERSION}. Logs also written to logs/endpoint-agent.log.")

    def _log(self, message: str) -> None:
        self.log_box.config(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")
        logger.info(message)

    def _on_install(self) -> None:
        url = self.url_entry.get().strip()
        code = self.code_entry.get().strip()
        if not url or not code:
            self._log("Enter both the platform URL and the registration code.")
            return
        self.install_button.config(state="disabled")
        self.status_var.set("Working…")
        threading.Thread(target=self._install_worker, args=(url, code), daemon=True).start()

    def _install_worker(self, url: str, code: str) -> None:
        try:
            self.root.after(0, self._log, f"Registering with {url} …")
            snapshot = collect()
            client = PlatformClient(base_url=url)
            result = client.register(
                registration_code=code,
                device_name=None,
                hostname=snapshot["hostname"],
                os_name=snapshot["os_name"],
                os_version=snapshot["os_version"],
                agent_version=AGENT_VERSION,
            )
            identity = DeviceIdentity(device_id=result["device_id"], organization_id=result["organization_id"], api_key=result["api_key"])
            save_identity(identity)
            PLATFORM_URL_PATH.write_text(url)
            self.root.after(0, self._log, "Registered. Device identity saved.")
        except Exception as exc:
            logger.exception("Registration failed")
            self.root.after(0, self._log, f"Registration failed: {exc}")
            self.root.after(0, self._on_worker_done, False)
            return

        self.root.after(0, self._log, "Installing Windows Service — a Windows permission prompt will appear, click Yes…")
        # --startup auto: without it pywin32 defaults to Manual start, which
        # means any reboot — including one triggered by our own "Restart
        # device" remote command — silently ends monitoring until someone
        # logs in and starts the service by hand.
        # pywin32's arg parser (getopt, not gnu_getopt) stops scanning for
        # flags at the first non-flag token — "--startup auto" must come
        # BEFORE "install" or it's silently swallowed as a positional arg
        # and HandleCommandLine prints usage() and exits 1 instead of
        # installing anything. Verified directly: `install --startup auto`
        # fails this way, `--startup auto install` doesn't.
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

        self.root.after(0, self._log, "Service started — running in the background as 'Madire Endpoint Agent'.")
        self.root.after(0, self._on_worker_done, True)

    def _on_worker_done(self, success: bool) -> None:
        self.install_button.config(state="normal", text="Re-register && reinstall service")
        self.status_var.set("Enrolled and running." if success else "Enrollment or service step failed — see log.")


def main() -> None:
    logging_setup.configure()
    root = tk.Tk()
    SetupWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
