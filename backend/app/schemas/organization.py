import uuid
from typing import Literal

from pydantic import EmailStr, Field

from app.schemas.common import OrmModel

OrganizationStatus = Literal["onboarding", "active", "inactive", "suspended"]


class OrganizationCreate(OrmModel):
    organization_name: str = Field(min_length=1, max_length=255)
    trading_name: str | None = Field(default=None, max_length=255)
    registration_number: str | None = Field(default=None, max_length=100)
    # One or more industries — drives the auto-created risk register (see
    # risk_auto_seed_service.py). Replaces the old single free-text field.
    industry_ids: list[uuid.UUID] = Field(default_factory=list)
    country: str | None = Field(default=None, max_length=100)
    # Bootstraps the client's first user in the same call — mirrors the
    # product spec's "owner adds a client" flow (Section 8).
    primary_admin_first_name: str = Field(min_length=1, max_length=100)
    primary_admin_last_name: str = Field(min_length=1, max_length=100)
    primary_admin_email: EmailStr


class OrganizationOut(OrmModel):
    organization_id: uuid.UUID
    organization_name: str
    trading_name: str | None
    registration_number: str | None
    industry: str | None
    industries: list[str] = []
    country: str | None
    status: OrganizationStatus
    is_internal: bool = False


class OrganizationCreatedOut(OrganizationOut):
    """
    Returned from the create call so the System Owner can copy the new
    admin's temporary password immediately. It also stays visible later on
    the organization's Users page (GET /organizations/{id}/users) until that
    admin activates their account — see migration 0010.
    """

    primary_admin_email: str
    temporary_password: str
