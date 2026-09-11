import uuid
from datetime import date, datetime
from typing import Literal

from app.schemas.common import OrmModel

RiskRating = Literal["low", "medium", "high", "critical"]
FindingStatus = Literal["open", "remediation_in_progress", "awaiting_retest", "closed", "reopened"]


class FindingCreate(OrmModel):
    finding_title: str
    finding_description: str | None = None
    risk_rating: RiskRating | None = None


class FindingOut(OrmModel):
    finding_id: uuid.UUID
    organization_id: uuid.UUID
    exception_id: uuid.UUID
    finding_title: str
    finding_description: str | None
    risk_rating: str | None
    status: str
    identified_at: datetime


class RootCauseCreate(OrmModel):
    root_cause_category: str | None = None
    description: str | None = None


class RootCauseOut(OrmModel):
    root_cause_id: uuid.UUID
    finding_id: uuid.UUID
    root_cause_category: str | None
    description: str | None


class RemediationActionCreate(OrmModel):
    action_description: str
    responsible_user_id: uuid.UUID | None = None
    target_date: date | None = None


class RemediationActionUpdate(OrmModel):
    status: Literal["pending", "in_progress", "completed", "overdue"]


class RemediationActionOut(OrmModel):
    remediation_id: uuid.UUID
    finding_id: uuid.UUID
    action_description: str
    responsible_user_id: uuid.UUID | None
    target_date: date | None
    status: str
    completed_at: datetime | None
    is_overdue: bool = False


class RetestCreate(OrmModel):
    audit_test_id: uuid.UUID
    result: Literal["pass", "fail"]
    comments: str | None = None


class RetestOut(OrmModel):
    retest_id: uuid.UUID
    finding_id: uuid.UUID
    audit_test_id: uuid.UUID
    retest_date: datetime
    result: str | None
    performed_by: uuid.UUID | None
    comments: str | None


class TraceNode(OrmModel):
    level: str  # finding | exception | execution | audit_test | control | risk | business_process
    id: str
    label: str


class FindingTrace(OrmModel):
    chain: list[TraceNode]
