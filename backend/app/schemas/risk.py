import uuid
from typing import Literal

from app.schemas.common import OrmModel

RiskRating = Literal["low", "medium", "high", "critical"]


class RiskCategoryOut(OrmModel):
    risk_category_id: uuid.UUID
    category_name: str
    description: str | None


class RiskCreate(OrmModel):
    risk_name: str
    risk_description: str | None = None
    process_id: uuid.UUID | None = None
    risk_category_id: uuid.UUID | None = None
    inherent_risk_rating: RiskRating | None = None
    residual_risk_rating: RiskRating | None = None
    risk_owner_id: uuid.UUID | None = None


class RiskUpdate(OrmModel):
    """Any auto-created risk (or manual one) can be edited afterward."""

    risk_name: str | None = None
    risk_description: str | None = None
    process_id: uuid.UUID | None = None
    risk_category_id: uuid.UUID | None = None
    inherent_risk_rating: RiskRating | None = None
    residual_risk_rating: RiskRating | None = None
    risk_owner_id: uuid.UUID | None = None


class RiskOut(OrmModel):
    risk_id: uuid.UUID
    organization_id: uuid.UUID
    risk_library_id: uuid.UUID | None
    process_id: uuid.UUID | None
    risk_category_id: uuid.UUID | None
    risk_name: str
    risk_description: str | None
    inherent_risk_rating: str | None
    residual_risk_rating: str | None
    risk_owner_id: uuid.UUID | None
    status: str
    # True once at least one control addressing this risk has been
    # activated (auto-linked by domain when the control is activated, or
    # manually linked) — drives the "shaded until mitigated" display.
    has_active_control: bool = False
