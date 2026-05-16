"""Simple async cache layer with pluggable backends.

Default backend: in-process memory (no persistence, no cross-process sharing).
Redis backend: install redis and pass ``cache_backend="redis://..."`` to
``CoreAdminConfig``.

Usage::

    cache = request.app.state.adminfoundry.cache
    await cache.set("my_key", {"data": 1}, ttl=60)
    val = await cache.get("my_key")   # {"data": 1}
    await cache.delete("my_key")
"""
from __future__ import annotations

import json
import time
from typing import Any


class InMemoryBackend:
    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float | None]] = {}

    async def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        val, expires = entry
        if expires is not None and time.monotonic() > expires:
            del self._store[key]
            return None
        return val

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        expires = time.monotonic() + ttl if ttl is not None else None
        self._store[key] = (value, expires)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def clear(self) -> None:
        self._store.clear()


class RedisBackend:
    """Redis backend — requires ``pip install redis``."""

    def __init__(self, url: str) -> None:
        try:
            import redis.asyncio as aioredis
        except ImportError as exc:
            raise RuntimeError(
                "Redis backend requires the redis package: pip install redis"
            ) from exc
        self._client = aioredis.from_url(url, decode_responses=True)

    async def get(self, key: str) -> Any | None:
        raw = await self._client.get(key)
        return json.loads(raw) if raw is not None else None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        await self._client.set(key, json.dumps(value), ex=ttl)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def clear(self) -> None:
        await self._client.flushdb()


class CacheBackend:
    """Protocol for cache backends."""
    async def get(self, key: str): ...
    async def set(self, key: str, value, ttl: int | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def clear(self) -> None: ...


def make_cache(backend_url: str | None) -> "CacheBackend":
    """Return the appropriate cache backend for *backend_url*."""
    if not backend_url or backend_url == "memory":
        return InMemoryBackend()
    if backend_url.startswith("redis"):
        return RedisBackend(backend_url)
    raise ValueError(f"Unsupported cache backend URL: {backend_url!r}")


# ---------------------------------------------------------------------------
# Raw Redis client — shared singleton for rate-limit, token-blacklist, tenancy
# ---------------------------------------------------------------------------

_redis_client = None


def get_redis():
    """Return a shared async Redis client, or None if unavailable."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    from adminfoundry.settings import settings
    if not settings.REDIS_URL:
        return None
    try:
        import redis.asyncio as aioredis
    except ImportError:
        return None
    _redis_client = aioredis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def clear_redis_client() -> None:
    """Reset the cached client — used in tests or after config changes."""
    global _redis_client
    _redis_client = None
