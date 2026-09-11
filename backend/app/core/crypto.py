"""
Symmetric encryption for credentials the platform must be able to read back
(unlike passwords, which are one-way hashed) — specifically, direct-cloud
data-source connection passwords. Gateway-based connections never need this:
their credentials stay on the client's own Gateway machine via
secret_reference and never reach this database at all.
"""
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

settings = get_settings()
_fernet = Fernet(settings.credential_encryption_key.encode())


def encrypt_secret(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored credential could not be decrypted — the encryption key may have changed") from exc
