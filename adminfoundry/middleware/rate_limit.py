import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# {path_prefix: (max_requests, window_seconds, http_method_or_None)}
_LIMITS: dict[str, tuple[int, int, str | None]] = {
    "/api/v1/auth/login":   (10,  60,   None),
    "/api/v1/auth/refresh": (30,  60,   None),
    "/api/v1/tenants":      (5,  3600, "POST"),
}

# key → list of hit timestamps within the current window
_store: dict[str, list[float]] = {}


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

            hits = _store.get(key, [])
            hits = [t for t in hits if t > cutoff]
            if len(hits) >= max_req:
                _store[key] = hits
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests"},
                    headers={"Retry-After": str(window)},
                )
            hits.append(now)
            _store[key] = hits
            break

        return await call_next(request)


def reset_rate_limiter() -> None:
    _store.clear()


def get_rate_limit_stats() -> dict:
    return {
        "configured_routes": list(_LIMITS.keys()),
        "limits": {
            k: {"max_requests": v[0], "window_seconds": v[1], "method": v[2] or "ANY"}
            for k, v in _LIMITS.items()
        },
    }
