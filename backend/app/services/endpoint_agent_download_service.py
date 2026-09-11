from pathlib import Path

# backend/app/services/endpoint_agent_download_service.py -> repo root -> endpoint-agent/
_REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = _REPO_ROOT / "endpoint-agent"
WINDOWS_EXE_PATH = SOURCE_DIR / "dist" / "MadireEndpointAgent.exe"


def build_endpoint_agent_archive(platform: str) -> tuple[bytes, str, str]:
    """
    The compiled application itself — MadireEndpointAgent.exe, built via
    PyInstaller from this same source (see endpoint-agent/build_entry.py) —
    downloaded directly, not wrapped in a zip. Windows-only: the agent's
    whole design (Windows Service, BitLocker/Defender/firewall checks) is
    Windows-specific; Linux/Unix hosts are monitored via the Gateway instead.
    """
    if platform != "windows":
        raise ValueError(f"Unsupported platform: {platform}")

    with open(WINDOWS_EXE_PATH, "rb") as fh:
        exe_bytes = fh.read()
    return exe_bytes, "MadireEndpointAgent.exe", "application/vnd.microsoft.portable-executable"
