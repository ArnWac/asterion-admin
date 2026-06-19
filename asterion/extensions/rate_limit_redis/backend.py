"""Redis-backed login rate limiter implementation (Review R7).

The window is a sliding one, modelled as a Redis sorted set per key whose
members are scored by their unix timestamp. ``record_failure`` appends an entry
and refreshes the key TTL; ``is_limited`` counts entries inside the window with
``ZCOUNT`` (read-only).

Core depends only on the :class:`~asterion.auth.rate_limiter.RateLimiterBackend`
Protocol, never on ``redis`` — this class is duck-typed against any async Redis
client, so it imports nothing from ``redis`` itself. See the package
``__init__`` for the install + wiring story.
"""

from __future__ import annotations

import time
import uuid
from typing import Any


class RedisLoginRateLimiter:
    """Sliding-window login limiter backed by Redis sorted sets.

    Implements :class:`asterion.auth.rate_limiter.RateLimiterBackend`.
    """

    def __init__(
        self,
        client: Any,
        *,
        max_failures: int = 5,
        window_seconds: int = 15 * 60,
        namespace: str = "asterion:login-fail:",
    ) -> None:
        self._redis = client
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._namespace = namespace

    def _key(self, key: str) -> str:
        return f"{self._namespace}{key}"

    async def is_limited(self, key: str) -> bool:
        rkey = self._key(key)
        now = time.time()
        # Read-only count of failures still inside the window.
        count = await self._redis.zcount(rkey, now - self.window_seconds, "+inf")
        return int(count) >= self.max_failures

    async def record_failure(self, key: str) -> None:
        rkey = self._key(key)
        now = time.time()
        # Drop entries that have aged out, then append this failure. A random
        # suffix keeps the member unique even for two failures in the same
        # clock tick.
        await self._redis.zremrangebyscore(rkey, 0, now - self.window_seconds)
        await self._redis.zadd(rkey, {f"{now}:{uuid.uuid4().hex}": now})
        # Let Redis reclaim the key once the whole window has passed.
        await self._redis.expire(rkey, self.window_seconds)

    async def clear(self, key: str) -> None:
        await self._redis.delete(self._key(key))


__all__ = ["RedisLoginRateLimiter"]
