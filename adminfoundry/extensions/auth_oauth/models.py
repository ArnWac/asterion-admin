"""ORM model for the OAuth/OIDC extension — Phase 8b.

One table: ``external_identities``. Each row links a verified IdP-side
identity (``provider`` + ``provider_subject``) to a framework
``User`` row. The OAuth callback handler does either:

* a lookup-only ``(provider, sub) -> user_id`` — happy path, the user
  has logged in before;
* a find-or-create — only when the host app's ``UserProvider`` opted
  into ``auto_create_users=True``.

What's intentionally NOT here:

* No ``access_token`` / ``refresh_token`` / ``id_token`` columns.
  This is login-only — we don't proxy API calls to the IdP, so
  there's no reason to retain tokens. An extension that needs offline
  access would add its own table for that — keep the surface small
  here so leaks have less to steal.
* No "last login" timestamp on this row. Use the existing ``AuditLog``
  for that — duplicating it here would be a second source of truth.
* No soft delete. Unlinking deletes the row; the audit log records who
  did it. If we ever need "tombstone an identity but keep the row",
  add it then.

Migration story: the framework ships **no** migration for this table.
Host apps that wire ``OAuthExtension`` are responsible for running
``alembic revision --autogenerate`` against their own env.py after
importing the extension — adminfoundry's migration directory stays
free of extension-specific revisions. See ``docs/extensions.md`` § DB
models.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from adminfoundry.models.base import GUID, GlobalModel

if TYPE_CHECKING:
    from adminfoundry.models.user import User


class ExternalIdentity(GlobalModel):
    """One verified link between an IdP identity and a framework User.

    ``(provider, provider_subject)`` is unique — one Google subject can
    only resolve to one user. ``user_id`` is indexed (not unique) so a
    single user CAN link multiple providers (Google + GitHub + …).
    """

    __tablename__ = "external_identities"
    __table_args__ = (
        # The fundamental invariant: an IdP's stable subject identifier
        # maps to at most one framework user. If the same Google sub
        # showed up linked to two users we'd have a credential confusion
        # vulnerability — UNIQUE is the database-level guard against that.
        UniqueConstraint(
            "provider",
            "provider_subject",
            name="uq_external_identity_provider_subject",
        ),
        # Indexed-but-not-unique: a user can hold multiple identities
        # (Google work account + Google personal + GitHub). This index
        # backs the "show me my linked accounts" query in any future
        # "External Identities" admin.
        Index("ix_external_identity_user_id", "user_id"),
    )

    provider: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="OAuthProviderConfig.id — 'google', 'github', etc.",
    )

    provider_subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="IdP-side stable subject identifier — the OIDC 'sub' claim.",
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Captured for audit/debugging. Not used for identity resolution —
    # the (provider, provider_subject) pair is the authoritative key.
    # Email at provider may change over time; provider_subject is the
    # stable handle. Keeping the email at link time helps support staff
    # answer "which Google account is linked to this user?" without
    # round-tripping to Google.
    email_at_provider: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    picture_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    hosted_domain: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Google Workspace hosted-domain (hd claim), if present.",
    )

    user: Mapped[User] = relationship(
        "User",
        # No back_populates: User does not declare a `.external_identities`
        # collection. Keeping the relationship one-way means User stays
        # free of any auth_oauth dependency — the extension can be
        # uninstalled without breaking the core User class.
    )
