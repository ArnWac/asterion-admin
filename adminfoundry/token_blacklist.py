import time
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from adminfoundry.models.revoked_token import RevokedToken

# jti → unix timestamp of expiry (in-memory hot cache for the current process)
_revoked: dict[str, float] = {}

_REDIS_PREFIX = "bl:"


async def blacklist_token(jti: str, exp: float | int, db: AsyncSession) -> None:
    exp_f = float(exp)
    _revoked[jti] = exp_f

    from adminfoundry.cache import get_redis
    client = get_redis()
    if client:
        ttl = max(1, int(exp_f - time.time()))
        await client.setex(f"{_REDIS_PREFIX}{jti}", ttl, "1")

    exp_dt = datetime.fromtimestamp(exp_f, tz=timezone.utc)
    db.add(RevokedToken(jti=jti, exp=exp_dt))
    await db.flush()


async def is_blacklisted(jti: str, db: AsyncSession) -> bool:
    now = time.time()

    # 1. In-memory hot cache (fastest — same process, no I/O)
    cached_exp = _revoked.get(jti)
    if cached_exp is not None:
        return cached_exp > now

    # 2. Redis (shared across workers — authoritative when configured)
    from adminfoundry.cache import get_redis
    client = get_redis()
    if client:
        result = await client.get(f"{_REDIS_PREFIX}{jti}")
        return result is not None  # TTL-expiry handled by Redis

    # 3. DB fallback (single-worker or cold start without Redis)
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
    result = await db.execute(
        select(RevokedToken).where(
            RevokedToken.jti == jti,
            RevokedToken.exp > now_dt,
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        _revoked[jti] = row.exp.timestamp()  # warm in-memory cache
    return row is not None


def clear_blacklist() -> None:
    _revoked.clear()
