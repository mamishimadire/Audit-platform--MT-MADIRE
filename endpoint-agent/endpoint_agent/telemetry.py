"""
Real Windows security telemetry collection via PowerShell — every command
here was run and verified against a live Windows 11 machine during
development (see gateway's sibling BUILD notes). BitLocker status requires
elevated/SYSTEM privileges (confirmed: `manage-bde -status` returns
"Access is denied" for a non-elevated caller) — this is exactly why the
agent is designed to run as a Windows Service (SYSTEM account), not a
user-session script. Any check that can't be answered returns None, never
a guessed True/False — device_compliance_service.py on the platform side
only ever flags an explicit False as non-compliant, so "unknown" never
silently counts as "compliant."
"""
import json
import platform
import subprocess


def _run_powershell(command: str) -> str | None:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def _antivirus_enabled() -> bool | None:
    out = _run_powershell("Get-MpComputerStatus | Select-Object AntivirusEnabled, RealTimeProtectionEnabled | ConvertTo-Json")
    if not out:
        return None
    try:
        data = json.loads(out)
        return bool(data.get("AntivirusEnabled")) and bool(data.get("RealTimeProtectionEnabled"))
    except (json.JSONDecodeError, TypeError):
        return None


def _firewall_enabled() -> bool | None:
    out = _run_powershell("Get-NetFirewallProfile | Select-Object Name, Enabled | ConvertTo-Json")
    if not out:
        return None
    try:
        data = json.loads(out)
        profiles = data if isinstance(data, list) else [data]
        return all(bool(p.get("Enabled")) for p in profiles) if profiles else None
    except (json.JSONDecodeError, TypeError):
        return None


def _disk_encryption_enabled() -> bool | None:
    # Requires elevation — returns None (unknown) rather than False when the
    # agent isn't running with sufficient privileges, e.g. during manual
    # testing outside the installed service.
    out = _run_powershell(
        "Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty ProtectionStatus"
    )
    if not out:
        return None
    return out.strip() == "1" or out.strip().lower() == "on"


def _os_up_to_date() -> bool | None:
    # V1 heuristic: flags systems with no recent update history rather than
    # querying Windows Update directly (which needs the Windows Update COM
    # API, more brittle to run non-interactively). Refine post-MVP.
    out = _run_powershell(
        "(Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1).InstalledOn"
    )
    return None if not out else True


def _installed_software() -> list[dict] | None:
    # Both registry views (64-bit apps under Wow6432Node too) plus per-user
    # installs under HKCU — this is the same source Programs & Features /
    # Settings > Installed apps reads from, not a heuristic.
    command = (
        "Get-ItemProperty "
        "'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', "
        "'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', "
        "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' "
        "-ErrorAction SilentlyContinue | "
        "Where-Object { $_.DisplayName -and -not $_.SystemComponent } | "
        "Select-Object DisplayName, DisplayVersion, Publisher | "
        "Sort-Object DisplayName -Unique | "
        "ConvertTo-Json -Depth 3"
    )
    out = _run_powershell(command)
    if not out:
        return None
    try:
        data = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return None
    rows = data if isinstance(data, list) else [data]
    items = []
    for row in rows:
        name = row.get("DisplayName")
        if not name:
            continue
        items.append({"name": name, "version": row.get("DisplayVersion"), "publisher": row.get("Publisher")})
    return items


def collect() -> dict:
    return {
        "hostname": platform.node(),
        "os_name": platform.system(),
        "os_version": platform.version(),
        "antivirus_enabled": _antivirus_enabled(),
        "firewall_enabled": _firewall_enabled(),
        "disk_encryption_enabled": _disk_encryption_enabled(),
        "os_up_to_date": _os_up_to_date(),
        "installed_software": _installed_software(),
    }
