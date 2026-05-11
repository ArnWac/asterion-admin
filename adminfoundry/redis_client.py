from __future__ import annotations

try:
    from redis.asyncio import Redis as _Redis
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

_client = None


def get_redis():
    """Return a shared async Redis client, or None if REDIS_URL is unset or redis[extra] not installed."""
    global _client
    if _client is not None:
        return _client
    from adminfoundry.settings import settings
    if not settings.REDIS_URL or not _REDIS_AVAILABLE:
        return None
    _client = _Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def clear_redis_client() -> None:
    """Reset the cached client — used in tests or after config changes."""
    global _client
    _client = None
