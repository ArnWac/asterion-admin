"""Single-token revocation store (Roadmap 3.2).

Complements the coarse ``User.token_version`` mechanism (which revokes
*every* token for a user at once) with per-token revocation by ``jti``.
``POST /auth/logout`` writes one row here; the two auth-validation paths
(``get_current_user`` dependency + ``BuiltinJWTAuthProvider``) reject any
token whose ``jti`` is present.

``expires_at`` mirrors the token's own ``exp`` so a periodic prune can
drop rows for tokens that have expired anyway (they can never be
replayed once expired, so the revocation record is then dead weight) —
see ``asterion audit prune`` for the analogous pattern.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from asterion.models.base import GUID, GlobalModel


class RevokedToken(GlobalModel):
    __tablename__ = "revoked_tokens"

    #: The token's ``jti`` claim. Unique — revoking the same token twice
    #: is idempotent at the application layer (we check existence first).
    jti: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )

    #: The user the token belonged to (for admin/audit visibility).
    #: Nullable because an external auth provider's subject may not be a
    #: framework User UUID.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        nullable=True,
        index=True,
    )

    #: Mirror of the token's ``exp`` so a prune job can drop dead rows.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    #: Free-text reason — ``"logout"`` for the single-token endpoint,
    #: room for ``"security_reset"`` / ``"admin_revoke"`` later.
    reason: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
