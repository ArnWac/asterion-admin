"""Liveness + readiness endpoints.

``GET /healthz`` — liveness. Returns 200 as long as the process is up.
                  Suitable for kubernetes ``livenessProbe`` or a load
                  balancer's "still alive?" check.

``GET /readyz``  — readiness. Returns 200 when the database is reachable
                  and 503 otherwise. Suitable for kubernetes
                  ``readinessProbe``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter()


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    """Process is alive. Does not touch the database."""
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(request: Request) -> JSONResponse:
    """Process can serve traffic. Pings the database."""
    runtime = getattr(getattr(request.app, "state", None), "asterion", None)
    if runtime is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "error", "db": "no runtime"},
        )

    try:
        async with runtime.db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "error", "db": "unreachable", "detail": str(exc)},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ok", "db": "ok"},
    )
