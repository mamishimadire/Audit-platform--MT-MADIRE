import uuid

from pydantic import EmailStr, Field

from app.schemas.common import OrmModel


class TokenResponse(OrmModel):
    access_token: str
    token_type: str = "bearer"


class ActivateAccountRequest(OrmModel):
    email: EmailStr
    temporary_password: str
    new_password: str = Field(min_length=12, max_length=128)


class UserOut(OrmModel):
    user_id: uuid.UUID
    organization_id: uuid.UUID | None
    first_name: str
    last_name: str
    email: str
    status: str
    roles: list[str] = []
