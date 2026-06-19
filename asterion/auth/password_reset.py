"""Password-reset token issuance/consumption + the notifier SPI (3.3).

The framework owns the token lifecycle (generate → hash → store →
verify → consume) but NOT delivery: sending the reset link is
app-specific (SMTP, SES, a transactional-email API), so it goes through
a :class:`PasswordResetNotifier` the app supplies to ``create_admin``.
The default :class:`LoggingPasswordResetNotifier` logs the token at
WARNING for local development and is explicitly unsafe for production.

Security properties:
* The raw token is returned to the notifier exactly once and never
  stored — only its SHA-256 hash lands in ``password_reset_tokens``.
* Tokens are single-use (``used_at``) and short-lived (``expires_at``).
* Consuming a token bumps ``User.token_version`` so every existing
  session for that user is invalidated on password change.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.models.password_reset_token import PasswordResetToken
from asterion.models.user import User
from asterion.notifications.base import Notifier


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@runtime_checkable
class PasswordResetNotifier(Notifier, Protocol):
    """Delivers a password-reset token to the user.

    Implementations send an email / SMS / push containing a link with
    the raw ``token``. The framework calls this once per reset request
    for a known, active user. Must not raise for normal delivery
    failures the app wants to swallow — the route treats a notifier
    exception as a server error only if it propagates.

    Extends :class:`~asterion.notifications.Notifier` (the marker
    Protocol) so registration into
    :class:`~asterion.notifications.NotifierRegistry` is
    well-typed without a duplicate inheritance step.
    """

    async def send_reset(
        self,
        *,
        email: str,
        token: str,
        request: Request | None = None,
    ) -> None: ...


class LoggingPasswordResetNotifier:
    """Default notifier — logs the reset token at WARNING.

    Intended for local development so the flow is testable without an
    email backend. **Unsafe for production**: tokens in logs are a
    credential leak. Replace via ``create_admin(password_reset_notifier=...)``.
    """

    def __init__(self) -> None:
        import logging

        self._logger = logging.getLogger("asterion.auth.password_reset")

    async def send_reset(
        self,
        *,
        email: str,
        token: str,
        request: Request | None = None,
    ) -> None:
        self._logger.warning(
            "Password reset requested for %s. Reset token (dev only): %s",
            email,
            token,
        )


async def create_password_reset(
    session: AsyncSession,
    *,
    user: User,
    ttl_minutes: int,
) -> str:
    """Generate a reset token for ``user``, store its hash, return the
    raw token (to hand to the notifier). The raw token is never
    persisted."""
    raw = secrets.token_urlsafe(32)
    session.add(
        PasswordResetToken(
            token_hash=_hash_token(raw),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
        )
    )
    await session.flush()
    return raw


async def consume_password_reset(
    session: AsyncSession,
    *,
    raw_token: str,
) -> User | None:
    """Verify + consume a reset token, returning the owning ``User``.

    Returns ``None`` (and consumes nothing) when the token is unknown,
    already used, or expired. On success marks the row ``used_at`` —
    the caller then sets the new password + bumps token_version.
    """
    row = (
        await session.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == _hash_token(raw_token)
            )
        )
    ).scalar_one_or_none()

    if row is None or row.used_at is not None:
        return None
    # SQLite returns naive datetimes for DateTime(timezone=True); coerce
    # to aware UTC before comparing so the check works on both backends.
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires < datetime.now(UTC):
        return None

    user = await session.get(User, row.user_id)
    if user is None:
        return None

    row.used_at = datetime.now(UTC)
    await session.flush()
    return user
