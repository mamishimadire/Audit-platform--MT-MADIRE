from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    # Symmetric key for direct-cloud-connection credentials at rest — kept
    # separate from jwt_secret_key on purpose: rotating the JWT signing key
    # (e.g. to invalidate sessions) must never make every stored database
    # password unreadable. Must be a urlsafe-base64 32-byte Fernet key.
    credential_encryption_key: str
    access_token_expire_minutes: int = 30
    environment: str = "development"
    cors_origins: str = "http://localhost:5173"
    # MT AUDIT's own registered HubSpot app credentials (one app for the
    # whole platform — each client authorizes it against their own HubSpot
    # account, same as any third-party HubSpot integration). Optional so the
    # app still starts without them; the connect flow itself raises a clear
    # error if a client tries to use it before these are configured.
    hubspot_client_id: str | None = None
    hubspot_client_secret: str | None = None
    hubspot_redirect_uri: str | None = None
    # Where to send the user's browser back to after the OAuth callback.
    frontend_base_url: str = "http://localhost:5180"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
