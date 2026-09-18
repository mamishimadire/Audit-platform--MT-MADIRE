import uuid
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


class BehindScheduleItem(OrmModel):
    """One active monitoring schedule that's missed at least a full cycle
    of its own cadence — proof the "continuous" in continuous monitoring
    has actually stalled for this test, not just a number saying so."""

    audit_test_id: uuid.UUID
    test_code: str | None
    test_name: str
    frequency: str
    overdue_by_hours: float


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
    # Is continuous monitoring actually continuing, not just configured?
    # active_schedules_on_time / active_schedules_total is the answer —
    # a schedule counts as on time if it's not overdue by more than one
    # full cycle of its own frequency (see dashboard_service).
    active_schedules_total: int
    active_schedules_on_time: int
    reperformance_rate: float  # 0-100; 0 when active_schedules_total is 0 (nothing to measure yet)
    reperformances_last_30_days: int  # actual test runs in the last 30 days for tests under an active schedule
    behind_schedule: list[BehindScheduleItem] = []
