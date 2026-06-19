"""TOTP + backup-code helpers for 2FA (Roadmap 3.4).

Thin wrapper around ``pyotp`` plus the backup-code lifecycle. The model
layer stores the base32 secret on ``User.totp_secret`` and one hashed
row per backup code in ``two_factor_backup_codes``.

Verification accepts a small time window (``valid_window=1``) so a code
that ticks over during submission still passes — standard TOTP UX.
"""

from __future__ import annotations

import hashlib
import secrets

import pyotp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.models.two_factor_backup_code import TwoFactorBackupCode

#: Number of one-time backup codes minted at enable time.
BACKUP_CODE_COUNT = 10


def generate_secret() -> str:
    """A fresh base32 TOTP secret."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, *, account_name: str, issuer: str) -> str:
    """``otpauth://`` URI for QR-code enrollment in an authenticator app."""
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)


def verify_totp(secret: str, code: str) -> bool:
    """Verify a 6-digit TOTP code against the secret (±1 time step)."""
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def _hash_code(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_backup_codes(count: int = BACKUP_CODE_COUNT) -> list[str]:
    """Generate plaintext backup codes (shown to the user once).

    Format: ``xxxx-xxxx`` hex, easy to read back. The caller hashes
    them for storage via :func:`store_backup_codes`.
    """
    codes: list[str] = []
    for _ in range(count):
        raw = secrets.token_hex(4)  # 8 hex chars
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


async def store_backup_codes(
    session: AsyncSession,
    *,
    user_id,
    codes: list[str],
) -> None:
    """Persist the SHA-256 hashes of freshly generated backup codes.

    The caller is responsible for clearing any previous codes first
    (enable regenerates the whole set)."""
    for raw in codes:
        session.add(TwoFactorBackupCode(user_id=user_id, code_hash=_hash_code(raw)))
    await session.flush()


async def clear_backup_codes(session: AsyncSession, *, user_id) -> None:
    """Delete every backup code for a user (on disable / regenerate)."""
    rows = (
        (
            await session.execute(
                select(TwoFactorBackupCode).where(TwoFactorBackupCode.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        await session.delete(row)
    await session.flush()


async def consume_backup_code(
    session: AsyncSession,
    *,
    user_id,
    raw_code: str,
) -> bool:
    """Verify + consume one backup code. True when a matching unused
    code was found (and is now marked used); False otherwise."""
    if not raw_code:
        return False
    row = (
        await session.execute(
            select(TwoFactorBackupCode).where(
                TwoFactorBackupCode.user_id == user_id,
                TwoFactorBackupCode.code_hash == _hash_code(raw_code.strip()),
                TwoFactorBackupCode.used_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    from datetime import UTC, datetime

    row.used_at = datetime.now(UTC)
    await session.flush()
    return True
