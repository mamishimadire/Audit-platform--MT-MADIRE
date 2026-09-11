import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.request_context import set_client_ip

logging.basicConfig(level=logging.INFO)
settings = get_settings()

app = FastAPI(
    title="Audit-Native Continuous Monitoring & Assurance Platform",
    version="0.1.0",
    description="Version 1 API — Phase 1 (foundation, authentication, RBAC).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def capture_client_ip(request: Request, call_next):
    """Every audit_logs row should carry the requester's IP (Section 13),
    but threading it through every service function is a much larger, more
    invasive change than the value justifies — this makes it available to
    log_action() everywhere without touching existing call signatures."""
    set_client_ip(request.client.host if request.client else None)
    return await call_next(request)


app.include_router(api_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}
