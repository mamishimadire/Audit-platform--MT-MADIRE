from fastapi import APIRouter

from app.api.v1.routes import (
    audit_logs,
    audit_tests,
    auth,
    business_processes,
    business_units,
    control_library,
    controls,
    dashboard,
    data_mappings,
    data_sources,
    devices,
    exceptions,
    executions,
    findings,
    gateways,
    hubspot,
    industries,
    monitoring,
    organizations,
    platform_users,
    risks,
    roles,
    scope_assignments,
    test_rules,
    users,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
api_router.include_router(users.router)
api_router.include_router(platform_users.router)
api_router.include_router(roles.router)
api_router.include_router(business_units.router)
api_router.include_router(business_processes.router)
api_router.include_router(industries.router)
api_router.include_router(risks.router)
api_router.include_router(control_library.router)
api_router.include_router(controls.router)
api_router.include_router(audit_tests.router)
api_router.include_router(gateways.router)
api_router.include_router(data_sources.router)
api_router.include_router(data_mappings.router)
api_router.include_router(scope_assignments.router)
api_router.include_router(scope_assignments.admin_router)
api_router.include_router(test_rules.router)
api_router.include_router(monitoring.router)
api_router.include_router(executions.router)
api_router.include_router(exceptions.router)
api_router.include_router(findings.router)
api_router.include_router(dashboard.router)
api_router.include_router(audit_logs.router)
api_router.include_router(devices.router)
api_router.include_router(hubspot.router)
