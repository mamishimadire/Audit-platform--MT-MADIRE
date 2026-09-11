import uuid

from app.schemas.common import OrmModel


class BusinessProcessCreate(OrmModel):
    process_name: str
    process_code: str | None = None
    description: str | None = None
    process_owner_id: uuid.UUID | None = None


class BusinessProcessOut(OrmModel):
    process_id: uuid.UUID
    organization_id: uuid.UUID
    process_name: str
    process_code: str | None
    description: str | None
    process_owner_id: uuid.UUID | None
    status: str


class ProcessActivityCreate(OrmModel):
    activity_name: str
    activity_description: str | None = None
    activity_owner_id: uuid.UUID | None = None
    sequence_number: int = 0


class ProcessActivityOut(OrmModel):
    activity_id: uuid.UUID
    process_id: uuid.UUID
    activity_name: str
    activity_description: str | None
    activity_owner_id: uuid.UUID | None
    sequence_number: int
