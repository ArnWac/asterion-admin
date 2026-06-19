"""OAuth-capable user provider — Phase 8b.6.

After Phase 8b.5's verifier returns a validated claim set, the OAuth
callback needs to translate ``(provider, provider_subject)`` into an
:class:`AdminPrincipal`:

* if an ``ExternalIdentity`` row already links the subject to a user,
  load that user;
* if not, EITHER refuse (lookup-only mode, default) OR create a fresh
  ``User`` row + an ``ExternalIdentity`` link (auto-create mode).

This module separates that capability from the framework's read-side
:class:`UserProvider` Protocol. The split exists because:

1. Existing host apps with a custom ``UserProvider`` (LDAP, an
   external IAM, etc.) should not be forced to grow an OAuth-shaped
   write path they may never need.
2. The OAuth extension wants to write into its own
   ``external_identities`` table — coupling that to the core
   ``UserProvider`` Protocol would put extension-owned SQL on the
   core side of the import boundary.

The :class:`OAuthCapableUserProvider` Protocol is the seam external
identity systems implement to opt into auto-create. The
:class:`BuiltinOAuthUserProvider` is the default that operates on the
framework's built-in ``User`` model.

Security defaults of the builtin (all defendable in court):

* **Refuse to auto-link by email.** If ``allow_create=True`` and we
  find no ``ExternalIdentity`` for the subject BUT another user
  already owns that email address, we **refuse** — auto-linking
  would let an attacker who controls an email at the IdP take over
  an account they never proved ownership of. Account linking should
  be an explicit flow the user authenticates with both factors,
  which Phase 8b doesn't ship.
* **Refuse to create users without verified email.** If
  ``email_verified`` isn't True in the claims, refuse — unverified
  emails are owned by whoever can register them next, not the
  current bearer.
* **Inactive users return None.** A linked identity whose underlying
  user is deactivated logs in as anonymous (the route layer then 401s).
  Same behaviour as :class:`BuiltinSQLAlchemyUserProvider.get_by_id`.
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion.extensions.auth_oauth.dto import ExternalIdentityData
from asterion.extensions.auth_oauth.models import ExternalIdentity
from asterion.models.user import User
from asterion.providers.base import AdminPrincipal


class OAuthCapabilityError(Exception):
    """Base — the OAuth flow could not produce a principal.

    Subclasses tell the callback handler WHY so it can log/metric
    distinctly while always presenting the same generic message to
    the end user.
    """


class OAuthAutoCreateDisabledError(OAuthCapabilityError):
    """No identity link exists and ``allow_create`` is False."""


class OAuthEmailNotVerifiedError(OAuthCapabilityError):
    """Auto-create refused — IdP says the email isn't verified."""


class OAuthEmailCollisionError(OAuthCapabilityError):
    """Auto-create refused — a different user already owns this email.

    Auto-linking by email is a known account-takeover vector when the
    IdP doesn't perform domain ownership verification for the address.
    Refuse rather than guess.
    """


class OAuthUserInactiveError(OAuthCapabilityError):
    """Identity link exists but the underlying user is deactivated."""


@runtime_checkable
class OAuthCapableUserProvider(Protocol):
    """Optional capability — a UserProvider that can also link / create
    users from a verified external identity.

    Decoupled from the core :class:`UserProvider` Protocol so existing
    implementations don't have to grow a method they may never use.
    Custom IAM integrations implement THIS Protocol and pass an
    instance to ``OAuthExtension(user_provider=…)``.
    """

    async def find_or_create_by_external_identity(
        self,
        *,
        provider: str,
        provider_subject: str,
        claims: ExternalIdentityData,
        allow_create: bool,
        request: Request,
    ) -> AdminPrincipal: ...


class BuiltinOAuthUserProvider:
    """Default :class:`OAuthCapableUserProvider`.

    Operates on the framework's built-in ``User`` model + the
    extension's ``ExternalIdentity`` model. Suitable for apps that
    don't bring their own identity system.
    """

    async def find_or_create_by_external_identity(
        self,
        *,
        provider: str,
        provider_subject: str,
        claims: ExternalIdentityData,
        allow_create: bool,
        request: Request,
    ) -> AdminPrincipal:
        runtime = request.app.state.asterion
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)

        async with factory() as session, session.begin():
            # --- 1. Look for an existing identity link. ---
            identity = (
                await session.execute(
                    select(ExternalIdentity)
                    .where(ExternalIdentity.provider == provider)
                    .where(ExternalIdentity.provider_subject == provider_subject)
                )
            ).scalar_one_or_none()

            if identity is not None:
                user = (
                    await session.execute(select(User).where(User.id == identity.user_id))
                ).scalar_one_or_none()
                if user is None:
                    # Identity row references a deleted user — treat as
                    # the link being broken. The unique constraint
                    # prevents a duplicate identity from being created
                    # if the same subject ever returns; an admin must
                    # delete the orphan row manually.
                    raise OAuthAutoCreateDisabledError("identity link references a deleted user")
                if not user.is_active:
                    raise OAuthUserInactiveError(f"user {user.id} linked to identity is inactive")
                return _to_admin_principal(user)

            # --- 2. No identity. Auto-create policy. ---
            if not allow_create:
                raise OAuthAutoCreateDisabledError(
                    f"no link for ({provider!r}, …) and allow_create=False"
                )

            if claims.email_verified is not True:
                # The IdP either didn't send email_verified or sent
                # False. Either way, the email isn't trustworthy enough
                # to bootstrap a new account from.
                raise OAuthEmailNotVerifiedError("IdP did not verify the email address")

            email = claims.email_at_provider
            if not email:
                raise OAuthEmailNotVerifiedError("IdP did not provide an email address")

            # Refuse silent account linking by email — see module
            # docstring for the threat model.
            existing = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if existing is not None:
                raise OAuthEmailCollisionError(f"email {email!r} is already taken by another user")

            # --- 3. Create fresh User + identity link. ---
            new_user = User(
                id=uuid.uuid4(),
                email=email,
                # OAuth users never log in with a password. We store an
                # unusable hash placeholder so the column's NOT NULL
                # constraint is satisfied. The login route's
                # verify_password call against this would fail because
                # the placeholder isn't a valid bcrypt hash — that's
                # the intended behaviour.
                hashed_password="!oauth-only-account",
                full_name=claims.name,
                is_active=True,
                is_superadmin=False,
            )
            session.add(new_user)
            await session.flush()  # populate new_user.id from DB defaults

            session.add(
                ExternalIdentity(
                    provider=provider,
                    provider_subject=provider_subject,
                    user_id=new_user.id,
                    email_at_provider=email,
                    name=claims.name,
                    picture_url=claims.picture_url,
                    hosted_domain=claims.hosted_domain,
                )
            )
            return _to_admin_principal(new_user)


def _to_admin_principal(row: User) -> AdminPrincipal:
    """Mirror of providers.users._to_admin_principal — duplicated to
    avoid pulling that whole module's import surface (and its DB
    machinery) into the extension."""
    return AdminPrincipal(
        id=str(row.id),
        email=row.email,
        display_name=row.full_name,
        is_active=bool(row.is_active),
        is_superadmin=bool(row.is_superadmin),
    )
