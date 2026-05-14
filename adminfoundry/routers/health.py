from fastapi import APIRouter, Depends
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
    """Aggregated ops view: DB, active sessions, rate-limit summary, recent jobs."""
    # DB check
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "degraded"

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
        "recent_jobs": recent_jobs,
    }


