"""2FA backup codes (Roadmap 3.4).

When a user enables 2FA they receive a set of one-time backup codes to
use if they lose their authenticator. Only the SHA-256 hash of each
code is stored; the plaintext set is shown to the user exactly once at
enable time. Each code is single-use (``used_at``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from asterion.models.base import GUID, GlobalModel


class TwoFactorBackupCode(GlobalModel):
    __tablename__ = "two_factor_backup_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        nullable=False,
        index=True,
    )

    #: SHA-256 hex digest of the backup code. The plaintext is shown to
    #: the user once at enable time and never stored.
    code_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
