import time
from sqlalchemy import select, func
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from adminfoundry.models.rate_limit import RateLimitRequest

# {path_prefix: (max_requests, window_seconds, http_method_or_None)}
# method=None matches any HTTP method.
_LIMITS: dict[str, tuple[int, int, str | None]] = {
    "/api/v1/auth/login":   (10,  60,   None),
    "/api/v1/auth/refresh": (30,  60,   None),
    # Tenant creation: 5 per hour per IP — guards the onboarding / provisioning path.
    # POST only so that GET (list) and PATCH (update) are not throttled.
    "/api/v1/tenants":      (5,  3600, "POST"),
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        for prefix, (max_req, window, method) in _LIMITS.items():
            if not path.startswith(prefix):
                continue
            if method is not None and request.method != method:
                continue

            ip = request.client.host if request.client else "unknown"
            key = f"{prefix}:{method or 'ANY'}:{ip}"
            now = time.time()
            cutoff = now - window

            from adminfoundry.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                count_result = await db.execute(
                    select(func.count()).select_from(RateLimitRequest).where(
                        RateLimitRequest.key == key,
                        RateLimitRequest.ts > cutoff,
                    )
                )
                count = count_result.scalar_one()
                if count >= max_req:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Too many requests"},
                        headers={"Retry-After": str(window)},
                    )
                db.add(RateLimitRequest(key=key, ts=now))
                await db.commit()
            break
        return await call_next(request)


def reset_rate_limiter() -> None:
    """No-op — DB cleanup is handled by the clean_tables test fixture."""


def get_rate_limit_stats() -> dict:
    """Return in-process rate-limit config summary (no per-IP data)."""
    return {
        "configured_routes": list(_LIMITS.keys()),
        "limits": {
            k: {"max_requests": v[0], "window_seconds": v[1], "method": v[2] or "ANY"}
            for k, v in _LIMITS.items()
        },
    }
