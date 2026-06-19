"""Pluggable login rate limiter.

The framework ships :class:`InMemoryLoginRateLimiter` as the default. It
is single-process; under ``uvicorn --workers N`` the effective threshold
is roughly ``N x max_failures``. Multi-worker production deployments swap
in a Redis- or DB-backed backend that satisfies
:class:`RateLimiterBackend`. Core does NOT depend on Redis — Core only
depends on the Protocol shape.

The login flow uses a lowercased email as ``key``. Future call sites may
key on IP or on ``(email, ip)`` tuples; the Protocol is intentionally
opaque to that choice.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class RateLimiterBackend(Protocol):
    """Async-friendly Protocol for rate-limit backends.

    Methods are async so a Redis / DB implementation can issue network I/O
    without a separate sync/async split. The in-memory default still
    implements them as async functions; the awaits are essentially free.
    """

    async def is_limited(self, key: str) -> bool: ...

    async def record_failure(self, key: str) -> None: ...

    async def clear(self, key: str) -> None: ...


class InMemoryLoginRateLimiter:
    """Window-based in-process limiter. Default backend.

    Known limitations (documented per plan §PR-9):
      * not distributed — state lives in this process
      * resets on process restart
      * not sufficient for multi-worker production

    For multi-worker production swap in a backend that satisfies
    :class:`RateLimiterBackend`.
    """

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_seconds: int = 15 * 60,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}

    def _trim(self, key: str, now: float) -> list[float]:
        hits = [
            timestamp
            for timestamp in self._failures.get(key, [])
            if timestamp > now - self.window_seconds
        ]
        self._failures[key] = hits
        return hits

    async def is_limited(self, key: str) -> bool:
        now = time.time()
        hits = self._trim(key, now)
        return len(hits) >= self.max_failures

    async def record_failure(self, key: str) -> None:
        now = time.time()
        hits = self._trim(key, now)
        hits.append(now)
        self._failures[key] = hits

    async def clear(self, key: str) -> None:
        self._failures.pop(key, None)

    def reset(self) -> None:
        """Sync helper for tests; not part of :class:`RateLimiterBackend`."""
        self._failures.clear()


__all__ = [
    "InMemoryLoginRateLimiter",
    "RateLimiterBackend",
]
