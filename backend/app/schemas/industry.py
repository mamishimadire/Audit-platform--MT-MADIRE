import uuid

from app.schemas.common import OrmModel


class IndustryOut(OrmModel):
    industry_id: uuid.UUID
    industry_name: str
