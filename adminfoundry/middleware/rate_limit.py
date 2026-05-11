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

# Fallback in-memory store (single-process only)
_store: dict[str, list[float]] = {}


async def _check_redis(client, key: str, max_req: int, window: int) -> bool:
    """Sliding window via Redis sorted set. Returns True if the request should be blocked."""
    now = time.time()
    member = str(time.time_ns())
    async with client.pipeline(transaction=True) as pipe:
        pipe.zremrangebyscore(key, 0, now - window)
        pipe.zadd(key, {member: now})
        pipe.zcard(key)
        pipe.expire(key, window)
        results = await pipe.execute()
    return results[2] > max_req


def _check_memory(key: str, max_req: int, window: int) -> bool:
    """Sliding window in-memory. Returns True if the request should be blocked."""
    now = time.time()
    cutoff = now - window
    hits = [t for t in _store.get(key, []) if t > cutoff]
    if len(hits) >= max_req:
        _store[key] = hits
        return True
    hits.append(now)
    _store[key] = hits
    return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        for prefix, (max_req, window, method) in _LIMITS.items():
            if not path.startswith(prefix):
                continue
            if method is not None and request.method != method:
                continue

            ip = request.client.host if request.client else "unknown"
            key = f"rl:{prefix}:{method or 'ANY'}:{ip}"

            from adminfoundry.redis_client import get_redis
            client = get_redis()
            blocked = (
                await _check_redis(client, key, max_req, window)
                if client
                else _check_memory(key, max_req, window)
            )

            if blocked:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests"},
                    headers={"Retry-After": str(window)},
                )
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
