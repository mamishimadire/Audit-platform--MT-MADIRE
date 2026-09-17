import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from app.schemas.common import OrmModel


class TokenResponse(OrmModel):
    access_token: str
    token_type: str = "bearer"


class ActivateAccountRequest(OrmModel):
    email: EmailStr
    temporary_password: str
    new_password: str = Field(min_length=12, max_length=128)


class ChangePasswordRequest(OrmModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=128)


class UserOut(OrmModel):
    user_id: uuid.UUID
    organization_id: uuid.UUID | None
    first_name: str
    last_name: str
    email: str
    status: str
    roles: list[str] = []
    # Both computed at read time from password_changed_at (see
    # auth_service.password_expiry_status) — never stored. must_change_
    # password true forces a redirect to /profile everywhere else in the
    # app; password_reminder_days_remaining (only set once it's within
    # PASSWORD_REMINDER_DAYS) drives a non-blocking "change it soon" nudge.
    must_change_password: bool = False
    password_reminder_days_remaining: int | None = None
    # When the CURRENT password was set — the profile page shows this as
    # "Password last changed on <date> (N days ago)". None only for a
    # still-pending account that has never activated.
    password_changed_at: datetime | None = None
