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
    tests_passed: int  # status=pass — ran, and found nothing wrong
    failed_tests: int  # status=exception — ran, and found a real problem (never mapping/error noise)
    # status IN (mapping_required, not_testable, error) — ran but couldn't
    # produce a real result at all; kept separate from failed_tests so a
    # blocked/unmapped control never inflates "this control is failing."
    tests_blocked_total: int
    # Latest execution per test only, not the cumulative totals above — "is
    # this control passing right now", distinct from "how many runs ever
    # passed/failed" (a control that failed all week then got fixed reads
    # as currently passing, not still dragging failed_tests up).
    controls_currently_passing: int
    controls_currently_failing: int  # latest run's status=exception, specifically
    controls_needs_attention: int  # latest run's status IN (mapping_required, not_testable, error)
    exceptions_open: int
    exceptions_high_risk: int
    open_findings: int
    overdue_findings: int
    closed_findings: int
    remediation_rate: float  # closed findings / total findings that ever had a remediation cycle, 0-100
    gateway_health: GatewayHealthCounts
    executions_trend: list[TrendPoint]
    exceptions_trend: list[TrendPoint]
