"""ObservabilityExtension routes.

Contributes:
- GET /metrics  — Prometheus text exposition (no auth, protect via network policy)
- GET /api/v1/admin/metrics  — JSON snapshot (superadmin only)
"""
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from adminfoundry.dependencies import require_superadmin
from adminfoundry.models.user import User

prometheus_router = APIRouter(tags=["observability"])
admin_metrics_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@prometheus_router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics():
    """Prometheus-compatible text exposition of admin counters.

    No auth required — protect via network policy per Prometheus convention.
    Never exposes secrets, token internals, or protected field content.
    """
    from adminfoundry.extensions.observability.admin_metrics import get_snapshot
    from adminfoundry.services.session_security import session_security

    snap = get_snapshot()

    try:
        active_sessions = sum(1 for s in session_security._sessions.values() if s.is_active)
    except Exception:
        active_sessions = 0

    lines = [
        "# HELP adminfoundry_requests_total Total HTTP requests processed",
        "# TYPE adminfoundry_requests_total counter",
        f"adminfoundry_requests_total {snap['request_count']}",
        "",
        "# HELP adminfoundry_request_errors_total Total HTTP requests that resulted in an error",
        "# TYPE adminfoundry_request_errors_total counter",
        f"adminfoundry_request_errors_total {snap['request_errors']}",
        "",
        "# HELP adminfoundry_actions_total Total admin actions executed",
        "# TYPE adminfoundry_actions_total counter",
        f"adminfoundry_actions_total {snap['action_count']}",
        "",
        "# HELP adminfoundry_action_errors_total Total admin actions that failed",
        "# TYPE adminfoundry_action_errors_total counter",
        f"adminfoundry_action_errors_total {snap['action_errors']}",
        "",
        "# HELP adminfoundry_audit_write_failures_total Audit log write failures",
        "# TYPE adminfoundry_audit_write_failures_total counter",
        f"adminfoundry_audit_write_failures_total {snap['audit_write_failures']}",
        "",
        "# HELP adminfoundry_active_sessions Current active sessions",
        "# TYPE adminfoundry_active_sessions gauge",
        f"adminfoundry_active_sessions {active_sessions}",
    ]
    return "\n".join(lines) + "\n"


@admin_metrics_router.get("/metrics")
async def admin_metrics(
    _: User = Depends(require_superadmin),
):
    """Return admin operational metrics snapshot — no secrets or protected field content."""
    from adminfoundry.extensions.observability.admin_metrics import get_snapshot
    return get_snapshot()
