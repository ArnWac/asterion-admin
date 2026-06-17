"""RedisLoginRateLimiter conformance (Review R7).

Uses fakeredis' async client as a stand-in for ``redis.asyncio.Redis`` so the
backend's window / count / clear semantics are exercised without a real Redis
server. Also asserts the class structurally satisfies the
``RateLimiterBackend`` Protocol the login flow depends on.
"""

from __future__ import annotations

import time

import pytest
from fakeredis import aioredis

from adminfoundry.auth.rate_limiter import RateLimiterBackend
from adminfoundry.extensions.rate_limit_redis import RedisLoginRateLimiter


@pytest.fixture
def redis_client():
    return aioredis.FakeRedis()


def test_satisfies_backend_protocol(redis_client):
    limiter = RedisLoginRateLimiter(redis_client)
    assert isinstance(limiter, RateLimiterBackend)


@pytest.mark.asyncio
async def test_not_limited_below_threshold(redis_client):
    limiter = RedisLoginRateLimiter(redis_client, max_failures=3)
    await limiter.record_failure("user@example.com")
    await limiter.record_failure("user@example.com")
    assert await limiter.is_limited("user@example.com") is False


@pytest.mark.asyncio
async def test_limited_at_threshold(redis_client):
    limiter = RedisLoginRateLimiter(redis_client, max_failures=3)
    for _ in range(3):
        await limiter.record_failure("user@example.com")
    assert await limiter.is_limited("user@example.com") is True


@pytest.mark.asyncio
async def test_keys_are_independent(redis_client):
    limiter = RedisLoginRateLimiter(redis_client, max_failures=1)
    await limiter.record_failure("victim@example.com")
    assert await limiter.is_limited("victim@example.com") is True
    assert await limiter.is_limited("other@example.com") is False


@pytest.mark.asyncio
async def test_clear_resets_a_key(redis_client):
    limiter = RedisLoginRateLimiter(redis_client, max_failures=1)
    await limiter.record_failure("user@example.com")
    assert await limiter.is_limited("user@example.com") is True
    await limiter.clear("user@example.com")
    assert await limiter.is_limited("user@example.com") is False


@pytest.mark.asyncio
async def test_old_failures_outside_window_do_not_count(redis_client):
    # A failure recorded an hour ago must not count against a 60s window,
    # while a fresh one does. Inject the stale entry directly so the test is
    # deterministic regardless of clock resolution.
    limiter = RedisLoginRateLimiter(redis_client, max_failures=1, window_seconds=60)
    await redis_client.zadd(limiter._key("user@example.com"), {"stale": time.time() - 3600})
    assert await limiter.is_limited("user@example.com") is False

    await limiter.record_failure("user@example.com")
    assert await limiter.is_limited("user@example.com") is True
