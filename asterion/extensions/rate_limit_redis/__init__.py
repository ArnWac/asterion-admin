"""Redis-backed login rate limiter extension (Review R7).

Plug-in backend for :class:`asterion.auth.rate_limiter.RateLimiterBackend`.
Like :mod:`asterion.extensions.storage_s3`, this extension does NOT mount
routes or contribute to the admin context — it just exposes
:class:`RedisLoginRateLimiter`, which the host app passes to
``create_admin(login_rate_limiter=...)``::

    import redis.asyncio as aioredis
    from asterion import create_admin
    from asterion.extensions.rate_limit_redis import RedisLoginRateLimiter

    limiter = RedisLoginRateLimiter(aioredis.from_url("redis://localhost:6379"))
    app = create_admin(config=..., login_rate_limiter=limiter)

The login flow then throttles failed attempts across every worker / process /
instance that shares the Redis instance, instead of per-process like the
in-memory default. The login key is the lowercased email; a future change can
key on ``(email, ip)`` without touching this class.

Dependencies
------------

Requires an async Redis client. Install via the optional extras::

    pip install asterion-admin[rate-limit-redis]

This module imports nothing from ``redis`` — :class:`RedisLoginRateLimiter` is
duck-typed against any async client (``redis.asyncio.Redis``,
``fakeredis.aioredis.FakeRedis``, …), so importing it is always safe.
"""

from __future__ import annotations

from asterion.extensions.rate_limit_redis.backend import RedisLoginRateLimiter

__all__ = ["RedisLoginRateLimiter"]
