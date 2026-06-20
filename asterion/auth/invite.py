"""Member-invite token issuance + the invite-notifier SPI.

A tenant operator can onboard a brand-new admin user by email (see
:mod:`asterion.admin.member_router`). When the email maps to no existing
global user, the framework creates an **inactive, passwordless** ``User`` and
issues a single-use token the invitee redeems to set a password — at which
point the account is activated.

The token lifecycle reuses the password-reset machinery (the
``password_reset_tokens`` table is purpose-neutral: hash + user + expiry +
single-use), so an invite is completed at the existing
``/auth/password-reset/confirm`` endpoint, which now also flips
``User.is_active`` to True. What differs is **delivery**: an invitation reads
differently from a "you requested a reset" email, so it goes through its own
:class:`InviteNotifier` (same "framework owns the token, app owns delivery"
split as :class:`~asterion.auth.password_reset.PasswordResetNotifier`).

Security properties (inherited from the reset machinery):
* The raw token is handed to the notifier exactly once and never stored —
  only its SHA-256 hash lands in ``password_reset_tokens``.
* Tokens are single-use (``used_at``) and time-limited (``expires_at``).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.auth.password_reset import create_password_reset
from asterion.models.user import User
from asterion.notifications.base import Notifier


@runtime_checkable
class InviteNotifier(Notifier, Protocol):
    """Delivers a member-invite token to a newly-invited user.

    Implementations send an email / SMS containing a link with the raw
    ``token`` that lands the invitee on the password-set screen. The
    framework calls this once per invite for a freshly-created, inactive
    user. Mirrors :class:`~asterion.auth.password_reset.PasswordResetNotifier`
    so it registers cleanly into
    :class:`~asterion.notifications.NotifierRegistry`.
    """

    async def send_invite(
        self,
        *,
        email: str,
        token: str,
        tenant_slug: str | None = None,
        request: Request | None = None,
    ) -> None: ...


class LoggingInviteNotifier:
    """Default notifier — logs the invite token at WARNING.

    For local development so the invite flow is testable without an email
    backend. **Unsafe for production**: tokens in logs are a credential
    leak. Replace via ``create_admin(invite_notifier=...)``.
    """

    def __init__(self) -> None:
        import logging

        self._logger = logging.getLogger("asterion.auth.invite")

    async def send_invite(
        self,
        *,
        email: str,
        token: str,
        tenant_slug: str | None = None,
        request: Request | None = None,
    ) -> None:
        self._logger.warning(
            "Member invite issued for %s (tenant=%s). Invite token (dev only): %s",
            email,
            tenant_slug,
            token,
        )


async def create_invite(
    session: AsyncSession,
    *,
    user: User,
    ttl_minutes: int,
) -> str:
    """Generate an invite token for ``user`` and return the raw token.

    Thin alias over :func:`~asterion.auth.password_reset.create_password_reset`
    — the invite and reset flows share the single-use token table; only the
    delivery channel and TTL differ.
    """
    return await create_password_reset(session, user=user, ttl_minutes=ttl_minutes)
