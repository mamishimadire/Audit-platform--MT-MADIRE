from pathlib import Path

# backend/app/services/endpoint_agent_download_service.py -> repo root -> endpoint-agent/
_REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = _REPO_ROOT / "endpoint-agent"
WINDOWS_EXE_PATH = SOURCE_DIR / "dist" / "MadireEndpointAgent.exe"


def windows_exe(platform: str) -> tuple[Path, str, str]:
    """
    The compiled application itself — MadireEndpointAgent.exe, built via
    PyInstaller from this same source (see endpoint-agent/build_entry.py) —
    downloaded directly, not wrapped in a zip. Windows-only: the agent's
    whole design (Windows Service, BitLocker/Defender/firewall checks) is
    Windows-specific; Linux/Unix hosts are monitored via the Gateway instead.

    Returned as a FILE, to be streamed from disk by the route, never read into
    memory: the download endpoint is public, so loading the whole executable
    for every request would let a few concurrent requests exhaust a small server.
    """
    if platform != "windows":
        raise ValueError(f"Unsupported platform: {platform}")
    WINDOWS_EXE_PATH.stat()  # raises OSError if it is missing, so the route can say "try again shortly"
    return WINDOWS_EXE_PATH, "MadireEndpointAgent.exe", "application/vnd.microsoft.portable-executable"
