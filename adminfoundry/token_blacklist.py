import time
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from adminfoundry.models.revoked_token import RevokedToken

# jti → unix timestamp of expiry (in-memory hot cache)
_revoked: dict[str, float] = {}


async def blacklist_token(jti: str, exp: float | int, db: AsyncSession) -> None:
    exp_f = float(exp)
    _revoked[jti] = exp_f
    exp_dt = datetime.fromtimestamp(exp_f, tz=timezone.utc)
    db.add(RevokedToken(jti=jti, exp=exp_dt))
    await db.flush()


async def is_blacklisted(jti: str, db: AsyncSession) -> bool:
    now = time.time()
    # Fast in-memory check — avoids DB round-trip for recently revoked tokens
    cached_exp = _revoked.get(jti)
    if cached_exp is not None:
        return cached_exp > now

    # DB fallback: handles cold start or tokens revoked in another process
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
    result = await db.execute(
        select(RevokedToken).where(
            RevokedToken.jti == jti,
            RevokedToken.exp > now_dt,
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        _revoked[jti] = row.exp.timestamp()  # warm the cache
    return row is not None


def clear_blacklist() -> None:
    _revoked.clear()
