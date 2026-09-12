from datetime import date

from app.schemas.common import OrmModel


class GatewayHealthCounts(OrmModel):
    online: int = 0
    offline: int = 0
    pending: int = 0
    deregistered: int = 0


class TrendPoint(OrmModel):
    date: date
    count: int


class DashboardStats(OrmModel):
    active_monitoring_tests: int
    tests_executed_total: int
    tests_executed_today: int
    tests_passed: int  # completed executions that found zero exceptions — the control held
    failed_tests: int
    exceptions_open: int
    exceptions_high_risk: int
    open_findings: int
    overdue_findings: int
    closed_findings: int
    remediation_rate: float  # closed findings / total findings that ever had a remediation cycle, 0-100
    gateway_health: GatewayHealthCounts
    executions_trend: list[TrendPoint]
    exceptions_trend: list[TrendPoint]
