import time
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class _SlidingWindowLimiter:
    def __init__(self):
        self._windows: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        self._windows[key] = [t for t in self._windows[key] if t > cutoff]
        if len(self._windows[key]) >= max_requests:
            return False
        self._windows[key].append(now)
        return True

    def reset(self) -> None:
        self._windows.clear()


_limiter = _SlidingWindowLimiter()

# {path_prefix: (max_requests, window_seconds)}
_LIMITS: dict[str, tuple[int, int]] = {
    "/api/v1/auth/login": (10, 60),
    "/api/v1/auth/refresh": (30, 60),
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        for prefix, (max_req, window) in _LIMITS.items():
            if path.startswith(prefix):
                ip = request.client.host if request.client else "unknown"
                if not _limiter.is_allowed(f"{prefix}:{ip}", max_req, window):
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Too many requests"},
                        headers={"Retry-After": str(window)},
                    )
                break
        return await call_next(request)


def reset_rate_limiter() -> None:
    _limiter.reset()
