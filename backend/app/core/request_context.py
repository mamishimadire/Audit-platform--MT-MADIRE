"""
Makes the current request's client IP available to log_action() without
threading it through every service function's signature — that would mean
touching ~19 files' call chains for one field. A per-request ContextVar, set
once by middleware, is the standard non-invasive way to do this in an ASGI
app: each request gets its own isolated value, and code with no direct
access to the Request object (most service-layer functions) can still read
it. Falls back to None outside a request (e.g. a script run directly).
"""
from contextvars import ContextVar

_client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)


def set_client_ip(ip: str | None) -> None:
    _client_ip.set(ip)


def get_client_ip() -> str | None:
    return _client_ip.get()
