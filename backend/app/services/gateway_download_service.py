import io
import tarfile
from pathlib import Path

# backend/app/services/gateway_download_service.py -> repo root -> gateway/
_REPO_ROOT = Path(__file__).resolve().parents[3]
GATEWAY_SOURCE_DIR = _REPO_ROOT / "gateway"
WINDOWS_EXE_PATH = GATEWAY_SOURCE_DIR / "dist" / "MadireGateway.exe"

_EXCLUDE_DIR_NAMES = {".venv", "__pycache__", ".git", "dist", "build"}
_EXCLUDE_FILE_NAMES = {".gateway_identity.json", "config.yaml"}
# Windows-only files that don't belong in the Linux source package — the
# source layout is shared, the packaging isn't.
_WINDOWS_ONLY_NAMES = {"install.bat", "WINDOWS_README.md"}


def _iter_source_files(*, exclude_names: set[str]):
    for path in GATEWAY_SOURCE_DIR.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _EXCLUDE_DIR_NAMES for part in path.relative_to(GATEWAY_SOURCE_DIR).parts):
            continue
        if path.name in _EXCLUDE_FILE_NAMES or path.name in exclude_names or path.suffix == ".pyc":
            continue
        yield path


def build_gateway_archive(platform: str) -> tuple[bytes, str, str]:
    """
    Windows gets the compiled application itself — MadireGateway.exe, built
    via PyInstaller from this same source (see gateway/build_entry.py) —
    downloaded directly, not wrapped in a zip. It's a self-contained CLI
    (register / run / service install|start|stop|remove); nothing else
    needs to ship alongside it. Linux gets the source as a tarball, which is
    the normal, expected way to run server-side tooling there.
    """
    if platform == "windows":
        with open(WINDOWS_EXE_PATH, "rb") as fh:
            exe_bytes = fh.read()
        return exe_bytes, "MadireGateway.exe", "application/vnd.microsoft.portable-executable"

    if platform == "linux":
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
            for path in _iter_source_files(exclude_names=_WINDOWS_ONLY_NAMES):
                arcname = "gateway/" + str(path.relative_to(GATEWAY_SOURCE_DIR)).replace("\\", "/")
                info = tf.gettarinfo(str(path), arcname=arcname)
                # NTFS doesn't track the unix executable bit, so set it
                # explicitly for the launcher script regardless of host OS.
                info.mode = 0o755 if path.name == "run.sh" else 0o644
                with open(path, "rb") as fh:
                    tf.addfile(info, fh)
        return buffer.getvalue(), "audit-data-gateway-linux.tar.gz", "application/gzip"

    raise ValueError(f"Unsupported platform: {platform}")
