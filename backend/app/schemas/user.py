import uuid
from typing import Literal

from pydantic import EmailStr, Field

from app.schemas.common import OrmModel

UserStatus = Literal["pending", "active", "inactive", "locked"]


class UserCreate(OrmModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    # No password field — the system always generates a temporary password,
    # so nobody (internal staff or a client admin) ever has to invent one.
    role_names: list[str] = Field(default_factory=list)


class PlatformUserCreate(OrmModel):
    """Adds one of MT AUDIT's own internal staff — organization_id is always
    NULL for these, and only platform-scoped roles may be assigned."""

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    role_names: list[str] = Field(default_factory=list)


class EligibleApproverOut(OrmModel):
    """Who a dual-control request will actually go to — see
    app.services.user_service.list_users_with_permission_for_organization."""

    user_id: uuid.UUID
    first_name: str
    last_name: str
    email: str


class UserOut(OrmModel):
    user_id: uuid.UUID
    organization_id: uuid.UUID | None
    first_name: str
    last_name: str
    email: str
    status: UserStatus
    roles: list[str] = []
    # Only ever non-null while status == 'pending' — cleared the moment the
    # user activates their account. Visible here so it stays copyable in the
    # UI rather than shown once and lost.
    temporary_password: str | None = None
