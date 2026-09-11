import uuid

from app.schemas.common import OrmModel


class BusinessUnitCreate(OrmModel):
    business_unit_name: str
    business_unit_code: str | None = None
    parent_business_unit_id: uuid.UUID | None = None


class BusinessUnitOut(OrmModel):
    business_unit_id: uuid.UUID
    organization_id: uuid.UUID
    parent_business_unit_id: uuid.UUID | None
    business_unit_name: str
    business_unit_code: str | None
    status: str
