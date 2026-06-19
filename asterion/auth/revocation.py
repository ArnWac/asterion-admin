"""Single-token revocation helpers (Roadmap 3.2).

One place that both auth-validation paths (the ``get_current_user``
dependency and ``BuiltinJWTAuthProvider``) call to check whether a
token's ``jti`` has been revoked, plus the writer used by
``POST /auth/logout``.

The check is a single indexed lookup per request. That's an extra round
trip on the hot path; a future optimisation can cache the revoked set
per process with a short TTL, but correctness comes first — a revoked
token must never authenticate.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.models.revoked_token import RevokedToken


async def is_token_revoked(session: AsyncSession, jti: str) -> bool:
    """True when a token with this ``jti`` has been revoked.

    Cheap existence check against the unique ``jti`` index. ``None`` /
    empty jti is treated as not-revoked (the caller's token-decode step
    already rejects tokens without a jti)."""
    if not jti:
        return False
    found = (
        await session.execute(select(RevokedToken.id).where(RevokedToken.jti == jti).limit(1))
    ).first()
    return found is not None


async def revoke_token(
    session: AsyncSession,
    *,
    jti: str,
    user_id=None,
    expires_at: datetime | None = None,
    reason: str | None = None,
) -> bool:
    """Record a token revocation. Idempotent — revoking an already-
    revoked jti is a no-op and returns ``False``; a fresh revocation
    returns ``True``.

    The caller owns the transaction (the row flushes into the request
    session). ``expires_at`` should be the token's own ``exp`` so a
    prune job can later drop the row once the token is dead anyway.
    """
    if not jti:
        return False
    if await is_token_revoked(session, jti):
        return False
    session.add(
        RevokedToken(
            jti=jti,
            user_id=user_id,
            expires_at=expires_at,
            reason=reason,
        )
    )
    await session.flush()
    return True


def token_exp_as_datetime(payload: dict) -> datetime | None:
    """Read the JWT ``exp`` claim (unix seconds) as an aware datetime,
    for storing on the revocation row. Returns ``None`` when absent /
    malformed — the row just won't carry an expiry hint then."""
    exp = payload.get("exp")
    if exp is None:
        return None
    try:
        return datetime.fromtimestamp(int(exp), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None
