"""Password-reset token store (Roadmap 3.3).

A reset request writes one row here; the confirm step verifies + marks
it used. The raw token is NEVER stored — only its SHA-256 hash — so a
leaked DB snapshot can't be used to reset passwords. The token itself
lives only in the reset link sent to the user.

Single-use (``used_at`` set on confirm) + short TTL (``expires_at``).
A prune job can drop rows past ``expires_at``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from asterion.models.base import GUID, GlobalModel


class PasswordResetToken(GlobalModel):
    __tablename__ = "password_reset_tokens"

    #: SHA-256 hex digest of the raw token. Looked up on confirm; the
    #: raw token never touches the DB.
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        nullable=False,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    #: Set when the token is consumed. A token with a non-null
    #: ``used_at`` is rejected on subsequent confirms (single-use).
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
