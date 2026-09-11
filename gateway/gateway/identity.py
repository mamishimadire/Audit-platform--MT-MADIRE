"""
Post-registration identity: once a registration code is redeemed, the
Gateway holds its own long-lived device credential for all future
communication — the code itself plays no further role (product spec,
Section 5). This file is that credential, stored locally, never committed.
"""
import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IDENTITY_PATH = Path(".gateway_identity.json")


@dataclass
class GatewayIdentity:
    gateway_id: str
    organization_id: str
    api_key: str

    def auth_headers(self) -> dict[str, str]:
        return {"X-Gateway-Id": self.gateway_id, "X-Api-Key": self.api_key}


def save_identity(identity: GatewayIdentity, path: Path = DEFAULT_IDENTITY_PATH) -> None:
    path.write_text(json.dumps(identity.__dict__, indent=2))
    try:
        path.chmod(0o600)
    except NotImplementedError:
        pass  # chmod semantics differ on Windows; best-effort only


def load_identity(path: Path = DEFAULT_IDENTITY_PATH) -> GatewayIdentity:
    if not path.exists():
        raise FileNotFoundError(
            f"No gateway identity found at {path}. Run `python -m gateway.register` first."
        )
    return GatewayIdentity(**json.loads(path.read_text()))
