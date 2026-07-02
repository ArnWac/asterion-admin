"""ORM model for the service-accounts extension (ADR-0005).

One table: ``service_accounts``. Each row marks a framework ``User`` as a
token-only machine account and records its provisioning metadata (the tenant it
is bound to and its human label). This table — not a boolean on ``User`` — is
the extension's source of truth for "which users are service accounts"; core
keeps only the generic ``User.password_login_disabled`` auth mechanism.

Mirrors the ``auth_oauth`` extension's ``ExternalIdentity``: a global
(public-schema) table with a one-way relationship to ``User`` (no
``back_populates``), so the core ``User`` class carries no dependency on this
extension and the extension can be uninstalled without touching core.

Migration story: the framework ships **no** migration for this table. Host apps
that wire ``ServiceAccountsExtension`` run ``alembic revision --autogenerate``
against their own env.py — exactly like ``external_identities``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from asterion.models.base import GUID, GlobalModel

if TYPE_CHECKING:
    from asterion.models.user import User


class ServiceAccount(GlobalModel):
    """Marks a framework ``User`` as a token-only service / machine account.

    ``user_id`` is unique — a user is either a service account or not. ``label``
    names the dedicated ``service:<label>`` tenant role; ``tenant_id`` is the
    tenant the account is bound to.
    """

    __tablename__ = "service_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_service_account_user"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)

    user: Mapped[User] = relationship(
        "User",
        # One-way: User declares no `.service_account` collection, so the core
        # User class stays free of any service_accounts dependency.
    )
