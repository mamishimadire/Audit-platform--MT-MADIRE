import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import cast, Date, func, select
from sqlalchemy.orm import Session

from app.core.execution_status import EXCEPTION, NEEDS_ATTENTION, PASS
from app.models.audit_test import AuditTest
from app.models.data_source import Gateway
from app.models.evidence_exception import Exception_
from app.models.finding import Finding, RemediationAction
from app.models.monitoring import MonitoringSchedule, TestExecution
from app.schemas.dashboard import DashboardStats, GatewayHealthCounts, TrendPoint

_TREND_DAYS = 14
_OPEN_EXCEPTION_STATUSES = ("open", "awaiting_evidence", "in_progress")
_HIGH_RISK_SEVERITIES = ("critical", "high")


def _daily_counts(db: Session, date_column, join_clause, where_clause, since: datetime) -> dict:
    rows = db.execute(
        select(cast(date_column, Date).label("day"), func.count())
        .select_from(join_clause)
        .where(where_clause, date_column >= since)
        .group_by("day")
    )
    return {row.day: row.count for row in rows}


def get_dashboard_stats(db: Session, *, organization_id: uuid.UUID) -> DashboardStats:
    now = datetime.now(timezone.utc)
    today = now.date()
    since = now - timedelta(days=_TREND_DAYS - 1)

    active_monitoring_tests = db.scalar(
        select(func.count())
        .select_from(MonitoringSchedule)
        .join(AuditTest, AuditTest.audit_test_id == MonitoringSchedule.audit_test_id)
        .where(AuditTest.organization_id == organization_id, MonitoringSchedule.is_active.is_(True))
    ) or 0

    execution_join = TestExecution.__table__.join(AuditTest.__table__, AuditTest.audit_test_id == TestExecution.audit_test_id)
    execution_where = AuditTest.organization_id == organization_id

    tests_executed_total = db.scalar(select(func.count()).select_from(execution_join).where(execution_where)) or 0
    tests_executed_today = db.scalar(
        select(func.count()).select_from(execution_join).where(execution_where, cast(TestExecution.started_at, Date) == today)
    ) or 0
    failed_tests = db.scalar(
        select(func.count()).select_from(execution_join).where(execution_where, TestExecution.status == EXCEPTION)
    ) or 0
    tests_passed = db.scalar(
        select(func.count()).select_from(execution_join).where(execution_where, TestExecution.status == PASS)
    ) or 0
    tests_blocked_total = db.scalar(
        select(func.count()).select_from(execution_join).where(execution_where, TestExecution.status.in_(NEEDS_ATTENTION))
    ) or 0

    # "Currently passing/failing" is a genuinely different question from
    # the cumulative totals above — a control that failed every 30 seconds
    # for a week and was then fixed should read as passing NOW, not still
    # be dragging the historical failed_tests count around. DISTINCT ON
    # picks exactly the latest execution row per audit_test_id.
    latest_execution = (
        select(TestExecution.audit_test_id, TestExecution.status, TestExecution.exceptions_found)
        .distinct(TestExecution.audit_test_id)
        .select_from(execution_join)
        .where(execution_where)
        .order_by(TestExecution.audit_test_id, TestExecution.started_at.desc())
    ).subquery()

    controls_currently_passing = db.scalar(
        select(func.count()).select_from(latest_execution).where(latest_execution.c.status == PASS)
    ) or 0
    controls_currently_failing = db.scalar(
        select(func.count()).select_from(latest_execution).where(latest_execution.c.status == EXCEPTION)
    ) or 0
    controls_needs_attention = db.scalar(
        select(func.count()).select_from(latest_execution).where(latest_execution.c.status.in_(NEEDS_ATTENTION))
    ) or 0

    exception_join = (
        Exception_.__table__.join(TestExecution.__table__, TestExecution.execution_id == Exception_.execution_id)
        .join(AuditTest.__table__, AuditTest.audit_test_id == TestExecution.audit_test_id)
    )
    exception_where = AuditTest.organization_id == organization_id

    exceptions_open = db.scalar(
        select(func.count()).select_from(exception_join).where(exception_where, Exception_.status.in_(_OPEN_EXCEPTION_STATUSES))
    ) or 0
    exceptions_high_risk = db.scalar(
        select(func.count()).select_from(exception_join).where(
            exception_where, Exception_.status.in_(_OPEN_EXCEPTION_STATUSES), Exception_.severity.in_(_HIGH_RISK_SEVERITIES)
        )
    ) or 0

    total_findings = db.scalar(select(func.count()).select_from(Finding).where(Finding.organization_id == organization_id)) or 0
    open_findings = db.scalar(
        select(func.count()).select_from(Finding).where(Finding.organization_id == organization_id, Finding.status != "closed")
    ) or 0
    closed_findings = total_findings - open_findings

    overdue_findings = db.scalar(
        select(func.count(func.distinct(Finding.finding_id)))
        .select_from(Finding)
        .join(RemediationAction, RemediationAction.finding_id == Finding.finding_id)
        .where(
            Finding.organization_id == organization_id,
            Finding.status != "closed",
            RemediationAction.status != "completed",
            RemediationAction.target_date.is_not(None),
            RemediationAction.target_date < today,
        )
    ) or 0

    remediation_rate = round((closed_findings / total_findings) * 100, 1) if total_findings > 0 else 0.0

    gateway_rows = db.execute(
        select(Gateway.status, func.count()).where(Gateway.organization_id == organization_id).group_by(Gateway.status)
    )
    gateway_counts = {row[0]: row[1] for row in gateway_rows}
    gateway_health = GatewayHealthCounts(
        online=gateway_counts.get("online", 0),
        offline=gateway_counts.get("offline", 0),
        pending=gateway_counts.get("pending", 0),
        deregistered=gateway_counts.get("deregistered", 0),
    )

    execution_counts = _daily_counts(db, TestExecution.started_at, execution_join, execution_where, since)
    exception_counts = _daily_counts(db, Exception_.detected_at, exception_join, exception_where, since)

    days = [(since.date() + timedelta(days=i)) for i in range(_TREND_DAYS)]
    executions_trend = [TrendPoint(date=d, count=execution_counts.get(d, 0)) for d in days]
    exceptions_trend = [TrendPoint(date=d, count=exception_counts.get(d, 0)) for d in days]

    return DashboardStats(
        active_monitoring_tests=active_monitoring_tests,
        tests_executed_total=tests_executed_total,
        tests_executed_today=tests_executed_today,
        tests_passed=tests_passed,
        failed_tests=failed_tests,
        tests_blocked_total=tests_blocked_total,
        controls_currently_passing=controls_currently_passing,
        controls_currently_failing=controls_currently_failing,
        controls_needs_attention=controls_needs_attention,
        exceptions_open=exceptions_open,
        exceptions_high_risk=exceptions_high_risk,
        open_findings=open_findings,
        overdue_findings=overdue_findings,
        closed_findings=closed_findings,
        remediation_rate=remediation_rate,
        gateway_health=gateway_health,
        executions_trend=executions_trend,
        exceptions_trend=exceptions_trend,
    )
