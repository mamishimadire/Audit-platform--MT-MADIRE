"""Same one-time-secret pattern as gateway/gateway/identity.py — a device
holds its own long-lived credential after enrollment; the registration
code plays no further role once redeemed."""
import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IDENTITY_PATH = Path(".device_identity.json")


@dataclass
class DeviceIdentity:
    device_id: str
    organization_id: str
    api_key: str

    def auth_headers(self) -> dict[str, str]:
        return {"X-Device-Id": self.device_id, "X-Api-Key": self.api_key}


def save_identity(identity: DeviceIdentity, path: Path = DEFAULT_IDENTITY_PATH) -> None:
    path.write_text(json.dumps(identity.__dict__, indent=2))
    try:
        path.chmod(0o600)
    except NotImplementedError:
        pass


def load_identity(path: Path = DEFAULT_IDENTITY_PATH) -> DeviceIdentity:
    if not path.exists():
        raise FileNotFoundError(f"No device identity found at {path}. Run `endpoint_agent register` first.")
    return DeviceIdentity(**json.loads(path.read_text()))
