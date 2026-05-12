from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from adminfoundry.database import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "degraded"

    overall = "ok" if db_status == "ok" else "degraded"
    return {"status": overall, "db": db_status}


@router.get("/health/dashboard")
async def health_dashboard(db: AsyncSession = Depends(get_db)):
    """Aggregated ops view: DB, metrics counters, active sessions, recent jobs."""
    # DB check
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "degraded"

    # Metrics counters (in-process)
    from adminfoundry.extensions.observability.admin_metrics import get_snapshot
    metrics = get_snapshot()

    # Active sessions
    try:
        from adminfoundry.services.session_security import session_security
        active_sessions = sum(1 for s in session_security._sessions.values() if s.is_active)
    except Exception:
        active_sessions = None

    # Recent jobs (opt-in extension — may not be loaded)
    recent_jobs: list | None = None
    try:
        from sqlalchemy import select
        from adminfoundry.extensions.jobs.models import Job
        rows = (
            await db.execute(
                select(Job).order_by(Job.created_at.desc()).limit(5)
            )
        ).scalars().all()
        recent_jobs = [
            {"id": str(j.id), "action": j.action_name, "status": j.status.value, "created_at": j.created_at.isoformat()}
            for j in rows
        ]
    except Exception:
        pass

    # Rate-limit config summary
    rate_limit_info: dict | None = None
    try:
        from adminfoundry.middleware.rate_limit import get_rate_limit_stats
        rate_limit_info = get_rate_limit_stats()
    except Exception:
        pass

    overall = "ok" if db_status == "ok" else "degraded"
    return {
        "status": overall,
        "db": db_status,
        "active_sessions": active_sessions,
        "rate_limit": rate_limit_info,
        "metrics": metrics,
        "recent_jobs": recent_jobs,
    }


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics(db: AsyncSession = Depends(get_db)):
    """Prometheus-compatible text exposition of admin counters.

    No auth required intentionally — mirrors Prometheus convention of
    protecting /metrics via network policy, not HTTP auth.  Do NOT
    expose protected field content or token internals here.
    """
    from adminfoundry.extensions.observability.admin_metrics import get_snapshot
    from adminfoundry.services.session_security import session_security

    snap = get_snapshot()

    # Active sessions count
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
