"""
Password hashing (Argon2) and JWT access-token issuance/verification.

No plaintext password is ever logged, stored, or returned in a response.
"""
import hashlib
import secrets
import string
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

settings = get_settings()


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return _pwd_context.verify(plain_password, password_hash)


def create_access_token(
    *, user_id: UUID, organization_id: UUID | None, session_id: UUID | None = None, extra_claims: dict | None = None
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict = {
        "sub": str(user_id),
        "org": str(organization_id) if organization_id else None,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    # Names the one user_sessions row this token is good for (see
    # api.deps.get_current_user and users.current_session_id) — omitted
    # entirely (not even a null claim) when the caller has no session to
    # tie it to, so an old token minted before single-session enforcement
    # existed just never carries the claim at all, rather than a
    # meaningless "sid": null.
    if session_id is not None:
        payload["sid"] = str(session_id)
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Raises jwt.PyJWTError on invalid/expired token — caller maps this to 401."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


# --- Gateway registration codes & device credentials ---
# The registration code is short and human-typeable (redeemed once, during
# install). The device credential issued after redemption is high-entropy
# and long-lived; only its SHA-256 fingerprint is ever stored — the raw
# value is returned exactly once, to the gateway, and never logged.

_CODE_ALPHABET = string.ascii_uppercase + string.digits


def generate_registration_code() -> str:
    """Format: AAAA-AAAA-AAAA, matching the product spec's on-screen code."""
    groups = ["".join(secrets.choice(_CODE_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "-".join(groups)


def generate_gateway_api_key() -> str:
    return secrets.token_urlsafe(32)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
