"""Tests for the pluggable rate-limiter backend (plan §PR-9).

* :class:`RateLimiterBackend` is a runtime_checkable Protocol.
* :class:`InMemoryLoginRateLimiter` satisfies it.
* Custom in-memory backends written by users also satisfy it.
* The Protocol API is fully async.
"""

from __future__ import annotations

import pytest

from asterion.auth.rate_limiter import InMemoryLoginRateLimiter, RateLimiterBackend


def test_in_memory_limiter_satisfies_protocol():
    """isinstance check passes for the default backend."""
    limiter = InMemoryLoginRateLimiter()
    assert isinstance(limiter, RateLimiterBackend)


def test_custom_backend_satisfies_protocol():
    """A user-provided implementation with the three async methods is
    accepted at runtime by the Protocol."""

    class NoopBackend:
        async def is_limited(self, key: str) -> bool:
            return False

        async def record_failure(self, key: str) -> None:
            pass

        async def clear(self, key: str) -> None:
            pass

    assert isinstance(NoopBackend(), RateLimiterBackend)


def test_object_missing_method_is_not_a_backend():
    class Incomplete:
        async def is_limited(self, key: str) -> bool:
            return False

    # missing record_failure + clear
    assert not isinstance(Incomplete(), RateLimiterBackend)


@pytest.mark.asyncio
async def test_async_round_trip():
    limiter = InMemoryLoginRateLimiter(max_failures=2, window_seconds=60)
    assert await limiter.is_limited("x") is False
    await limiter.record_failure("x")
    await limiter.record_failure("x")
    assert await limiter.is_limited("x") is True
    await limiter.clear("x")
    assert await limiter.is_limited("x") is False


@pytest.mark.asyncio
async def test_window_expiry_releases_block(monkeypatch):
    """Failures outside the window are not counted."""

    fake_now = [1000.0]

    def _patched_time():
        return fake_now[0]

    monkeypatch.setattr("asterion.auth.rate_limiter.time.time", _patched_time)

    limiter = InMemoryLoginRateLimiter(max_failures=2, window_seconds=60)
    await limiter.record_failure("x")
    await limiter.record_failure("x")
    assert await limiter.is_limited("x") is True

    # Jump past the window
    fake_now[0] += 120
    assert await limiter.is_limited("x") is False


@pytest.mark.asyncio
async def test_keys_are_isolated():
    limiter = InMemoryLoginRateLimiter(max_failures=1, window_seconds=60)
    await limiter.record_failure("alice@example.com")
    assert await limiter.is_limited("alice@example.com") is True
    assert await limiter.is_limited("bob@example.com") is False
