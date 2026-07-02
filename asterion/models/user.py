from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from asterion.models.base import GlobalModel

if TYPE_CHECKING:
    from asterion.models.tenant_membership import TenantMembership


class User(GlobalModel):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )

    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    full_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    is_superadmin: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    token_version: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    #: When the account was deactivated (``is_active`` flipped to False via
    #: ``user disable``). Starts the G2 retention clock: the
    #: ``privacy retention-run`` job auto-anonymises accounts whose
    #: ``deactivated_at`` is older than ``user_anonymize_after_days``. ``None``
    #: while active; cleared again on re-enable.
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    #: Generic auth mechanism (ADR-0005): when ``True`` the account may not
    #: authenticate with a password and never receives a password-reset token,
    #: so it can only authenticate via a minted token (or be provisioned another
    #: way). Distinct from mere passwordlessness — an invited human is
    #: passwordless yet ``False`` here, so they still receive a reset token to
    #: set their first password. Set ``True`` by the service-accounts extension;
    #: the "service account" concept itself lives in that extension, not core.
    password_login_disabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    #: Base32 TOTP shared secret (Roadmap 3.4). Set during 2FA setup;
    #: ``totp_enabled`` flips to True only after the first code is
    #: verified at the enable step. ``None`` means no 2FA configured.
    totp_secret: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    #: Whether 2FA is active for this user. A secret can be present
    #: (pending) while this is False between setup and enable.
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    memberships: Mapped[list[TenantMembership]] = relationship(
        "TenantMembership",
        back_populates="user",
        cascade="all, delete-orphan",
    )
