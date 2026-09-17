"""
Import every model module here so that Base.metadata is fully populated
for Alembic autogenerate and for SQLAlchemy relationship resolution.
"""
from app.models.organization import Organization, OrganizationSetting, BusinessUnit
from app.models.rbac import User, Role, Permission, UserRole, RolePermission, UserSession, UserOrganizationScope
from app.models.business_process import BusinessProcess, ProcessActivity
from app.models.control_library import ControlLibraryEntry
from app.models.industry import Industry, OrganizationIndustry
from app.models.risk_library import RiskLibraryEntry
from app.models.risk_control import RiskCategory, Risk, Control, RiskControl
from app.models.data_source import (
    Gateway,
    DataSource,
    DataConnection,
    DataEntity,
    DataField,
)
from app.models.audit_test import AuditTest, ControlAuditTest, TestDataMapping, TestRule
from app.models.monitoring import MonitoringSchedule, TestExecution
from app.models.evidence_exception import Evidence, Exception_, ExceptionComment, ExceptionRecord, EvidenceRequest
from app.models.finding import Finding, FindingRootCause, RemediationAction, Retest
from app.models.audit_log import AuditLog
from app.models.device import Device, DevicePolicyChange, DeviceTelemetry

__all__ = [
    "Organization",
    "OrganizationSetting",
    "BusinessUnit",
    "User",
    "Role",
    "Permission",
    "UserRole",
    "RolePermission",
    "UserSession",
    "UserOrganizationScope",
    "BusinessProcess",
    "ProcessActivity",
    "ControlLibraryEntry",
    "Industry",
    "OrganizationIndustry",
    "RiskLibraryEntry",
    "RiskCategory",
    "Risk",
    "Control",
    "RiskControl",
    "Gateway",
    "DataSource",
    "DataConnection",
    "DataEntity",
    "DataField",
    "AuditTest",
    "ControlAuditTest",
    "TestDataMapping",
    "TestRule",
    "MonitoringSchedule",
    "TestExecution",
    "Evidence",
    "Exception_",
    "ExceptionRecord",
    "EvidenceRequest",
    "ExceptionComment",
    "Finding",
    "FindingRootCause",
    "RemediationAction",
    "Retest",
    "AuditLog",
    "Device",
    "DevicePolicyChange",
    "DeviceTelemetry",
]
