"""Operational middlewares: request IDs, access logs, safe response headers.

``RequestIDMiddleware``
    Accepts an inbound ``X-Request-ID`` header, generates a UUID4 if
    absent, exposes it via ``request.state.request_id``, and echoes it
    back as a response header. Anything that wants to correlate a log
    line, an audit row, or an error response with the original request
    reads ``request.state.request_id``.

``AccessLogMiddleware``
    Emits one ``logger.info("request", extra={...})`` per request with
    request_id, method, path, status_code, duration_ms, plus tenant_id
    and actor_user_id when those have been populated on ``request.state``
    by ``TenantMiddleware`` / ``get_current_user``.

``SecurityHeadersMiddleware``
    Adds the three baseline security headers the production-ready prompt
    requires (``X-Content-Type-Options``, ``Referrer-Policy``,
    ``X-Frame-Options``). A ``Content-Security-Policy`` is emitted only when
    ``CoreAdminConfig.content_security_policy`` is set (Review R14): the
    bundled UI uses inline config scripts that a strict ``script-src 'self'``
    would block, so the default is header-less, but API-first deployments with
    their own frontend can opt into a strict policy.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

REQUEST_ID_HEADER = "X-Request-ID"

access_logger = logging.getLogger("adminfoundry.access")


def _generate_request_id() -> str:
    return uuid.uuid4().hex


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming or _generate_request_id()
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Structured per-request log line (plan §PR-4 / roadmap §E5 core part).

    Logged at INFO via ``adminfoundry.access``. The :class:`JSONFormatter`
    from :mod:`adminfoundry.core.logging` picks up every contextual field
    we set on ``extra``.
    """

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            access_logger.exception(
                "request",
                extra=_log_extra(request, status_code=500, duration_ms=duration_ms),
            )
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        access_logger.info(
            "request",
            extra=_log_extra(request, status_code=response.status_code, duration_ms=duration_ms),
        )
        return response


def _log_extra(request: Request, *, status_code: int, duration_ms: float) -> dict:
    extra: dict[str, object] = {
        "request_id": getattr(request.state, "request_id", None),
        "method": request.method,
        "path": request.url.path,
        "status_code": status_code,
        "duration_ms": duration_ms,
    }
    # Reading SQLAlchemy-bound attributes after the request session has
    # closed can trigger DetachedInstanceError. We catch everything so a
    # logging failure can never turn a successful request into a 500.
    try:
        actor = getattr(request.state, "current_user", None)
        if actor is not None:
            actor_id = getattr(actor, "id", None)
            if actor_id is not None:
                extra["actor_user_id"] = str(actor_id)
    except Exception:
        pass
    try:
        tenant = getattr(request.state, "tenant", None)
        if tenant is not None:
            tenant_id = getattr(tenant, "id", None)
            if tenant_id is not None:
                extra["tenant_id"] = str(tenant_id)
    except Exception:
        pass
    return extra


_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, csp: str | None = None) -> None:
        super().__init__(app)
        self._csp = csp

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        # Review R14: emit a CSP only when configured. The bundled UI is not
        # compatible with a strict policy (inline config scripts), so the
        # default (None) stays header-less.
        if self._csp:
            response.headers.setdefault("Content-Security-Policy", self._csp)
        return response
