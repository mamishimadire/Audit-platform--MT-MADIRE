import logging
import sys
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.request_context import set_client_ip

logging.basicConfig(level=logging.INFO)
settings = get_settings()

# Gateway-owned connections get executed by an external Gateway process
# polling resolve_due_tests_for_gateway(). Direct connections (MongoDB
# Atlas, direct cloud SQL) have no such external process, so the backend
# has to be the thing that runs them — a plain background thread rather
# than a task queue, matching the Gateway's own "no message broker needed"
# philosophy (see gateway/gateway/main.py) at a scale where that's fine.
_DIRECT_EXECUTION_POLL_SECONDS = 30


def _direct_execution_loop() -> None:
    from app.db.session import SessionLocal
    from app.services.direct_execution_service import run_due_direct_tests

    logger = logging.getLogger("app.direct_execution")
    while True:
        db = SessionLocal()
        try:
            run_due_direct_tests(db)
        except Exception:  # noqa: BLE001 — a bad cycle must not kill the loop
            logger.exception("Direct-execution poll cycle failed; will retry next interval")
        finally:
            db.close()
        time.sleep(_DIRECT_EXECUTION_POLL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tests run against the same live database as the app itself (see
    # tests/conftest.py) — if any test ever instantiates a TestClient, this
    # loop must not come alive and start mutating real schedules/executions
    # as a side effect of running the suite.
    if "pytest" not in sys.modules:
        threading.Thread(target=_direct_execution_loop, daemon=True, name="direct-execution-loop").start()
    yield


app = FastAPI(
    title="Audit-Native Continuous Monitoring & Assurance Platform",
    version="0.1.0",
    description="Version 1 API — Phase 1 (foundation, authentication, RBAC).",
    lifespan=lifespan,
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
