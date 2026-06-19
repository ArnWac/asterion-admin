"""Tests for the OAuth-capable user provider — Phase 8b.6.

The Builtin's job is to turn a verified ``(provider, sub)`` pair into
an :class:`AdminPrincipal`, applying the security defaults documented
in ``user_provider.py``'s docstring:

* Lookup-only by default (``allow_create=False``).
* Refuse to auto-create without verified email.
* Refuse to silently link by email collision.
* Refuse to log in linked-but-deactivated users.

Each test corresponds to one of those rules. The "happy path" auto-
create test exists so the lookup-only / refused cases don't drift away
from a working baseline.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.auth.password import hash_password
from asterion.extensions.auth_oauth import (
    BuiltinOAuthUserProvider,
    ExternalIdentity,
    ExternalIdentityData,
    GoogleOIDCProvider,
    OAuthAutoCreateDisabledError,
    OAuthCapableUserProvider,
    OAuthEmailCollisionError,
    OAuthEmailNotVerifiedError,
    OAuthExtension,
    OAuthUserInactiveError,
)
from asterion.models.base import GlobalModel
from asterion.models.user import User
from asterion.security.protected_fields import reset_for_tests as reset_protected


@pytest.fixture(autouse=True)
def _isolate_protected_fields():
    reset_protected()
    yield
    reset_protected()


@pytest.fixture
def app(tmp_path):
    app = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'uop.db'}",
            secret_key="test-uop",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        extensions=[
            OAuthExtension(providers=[GoogleOIDCProvider(client_id="x", client_secret="y")])
        ],
    )
    runtime = app.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)

    asyncio.run(_setup())
    yield app
    asyncio.run(runtime.db.dispose())


def _fake_request(app) -> Request:
    """Build a minimal Request whose only role is to carry app.state.

    The provider only reads ``request.app.state.asterion`` — no
    headers, no body, no path matter.
    """

    # Cheapest stub that satisfies the access pattern:
    class _S:
        pass

    s = _S()
    s.app = app
    return s  # type: ignore[return-value]


def _claims(
    *,
    email: str | None = "alice@example.com",
    email_verified: bool | None = True,
    name: str | None = "Alice",
) -> ExternalIdentityData:
    return ExternalIdentityData(
        provider="google",
        provider_subject="sub-1",
        email_at_provider=email,
        email_verified=email_verified,
        name=name,
        picture_url="https://example.com/a.png",
    )


# --- Protocol conformance ---


def test_builtin_satisfies_oauth_capable_user_provider_protocol():
    """Structural conformance check — isinstance against the
    runtime_checkable Protocol succeeds without explicit subclassing.
    The OAuth callback uses isinstance to enforce that whoever's wired
    in actually supports the method."""
    assert isinstance(BuiltinOAuthUserProvider(), OAuthCapableUserProvider)


# --- lookup-only ---


def test_lookup_only_refuses_unknown_identity(app):
    """allow_create=False + no link → raises AutoCreateDisabled."""
    provider = BuiltinOAuthUserProvider()
    req = _fake_request(app)
    with pytest.raises(OAuthAutoCreateDisabledError):
        asyncio.run(
            provider.find_or_create_by_external_identity(
                provider="google",
                provider_subject="never-seen",
                claims=_claims(),
                allow_create=False,
                request=req,
            )
        )


def test_lookup_finds_existing_link(app):
    """allow_create=False + existing link → returns the linked user."""
    runtime = app.state.asterion
    factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)

    async def _seed() -> str:
        async with factory() as s, s.begin():
            u = User(
                email="bob@example.com",
                hashed_password=hash_password("hunter2-strong"),
                is_active=True,
            )
            s.add(u)
            await s.flush()
            s.add(
                ExternalIdentity(
                    provider="google",
                    provider_subject="bob-sub",
                    user_id=u.id,
                )
            )
            return str(u.id)

    user_id = asyncio.run(_seed())

    provider = BuiltinOAuthUserProvider()
    principal = asyncio.run(
        provider.find_or_create_by_external_identity(
            provider="google",
            provider_subject="bob-sub",
            claims=_claims(email="bob@example.com"),
            allow_create=False,
            request=_fake_request(app),
        )
    )
    assert principal.id == user_id
    assert principal.email == "bob@example.com"


def test_inactive_linked_user_raises_inactive_error(app):
    """Deactivated users behind a verified identity must NOT sign in —
    matches the legacy get_current_user 403 behaviour."""
    runtime = app.state.asterion
    factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)

    async def _seed():
        async with factory() as s, s.begin():
            u = User(
                email="dormant@example.com",
                hashed_password=hash_password("hunter2-strong"),
                is_active=False,
            )
            s.add(u)
            await s.flush()
            s.add(
                ExternalIdentity(
                    provider="google",
                    provider_subject="dormant-sub",
                    user_id=u.id,
                )
            )

    asyncio.run(_seed())

    provider = BuiltinOAuthUserProvider()
    with pytest.raises(OAuthUserInactiveError):
        asyncio.run(
            provider.find_or_create_by_external_identity(
                provider="google",
                provider_subject="dormant-sub",
                claims=_claims(),
                allow_create=False,
                request=_fake_request(app),
            )
        )


# --- auto-create happy path ---


def test_auto_create_makes_user_and_identity(app):
    provider = BuiltinOAuthUserProvider()
    principal = asyncio.run(
        provider.find_or_create_by_external_identity(
            provider="google",
            provider_subject="new-sub",
            claims=_claims(email="newuser@example.com"),
            allow_create=True,
            request=_fake_request(app),
        )
    )
    assert principal.email == "newuser@example.com"
    assert principal.display_name == "Alice"

    # Verify both rows actually landed in the DB.
    runtime = app.state.asterion
    factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)

    async def _check() -> tuple[int, int]:
        async with factory() as s:
            users = (
                (await s.execute(select(User).where(User.email == "newuser@example.com")))
                .scalars()
                .all()
            )
            identities = (
                (
                    await s.execute(
                        select(ExternalIdentity).where(
                            ExternalIdentity.provider_subject == "new-sub"
                        )
                    )
                )
                .scalars()
                .all()
            )
            return len(users), len(identities)

    assert asyncio.run(_check()) == (1, 1)


def test_auto_create_links_picture_and_domain(app):
    """The created ExternalIdentity carries the optional profile fields
    so support staff can answer 'which Google account is this?'."""
    provider = BuiltinOAuthUserProvider()
    claims = ExternalIdentityData(
        provider="google",
        provider_subject="hd-sub",
        email_at_provider="user@acme.example",
        email_verified=True,
        name="Acme User",
        picture_url="https://lh3.googleusercontent.com/a/abc",
        hosted_domain="acme.example",
    )
    asyncio.run(
        provider.find_or_create_by_external_identity(
            provider="google",
            provider_subject="hd-sub",
            claims=claims,
            allow_create=True,
            request=_fake_request(app),
        )
    )

    runtime = app.state.asterion
    factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)

    async def _fetch() -> ExternalIdentity:
        async with factory() as s:
            return (
                await s.execute(
                    select(ExternalIdentity).where(ExternalIdentity.provider_subject == "hd-sub")
                )
            ).scalar_one()

    row = asyncio.run(_fetch())
    assert row.picture_url == "https://lh3.googleusercontent.com/a/abc"
    assert row.hosted_domain == "acme.example"


# --- auto-create security defaults ---


def test_auto_create_refuses_unverified_email(app):
    """The IdP says email_verified=False — refuse, the email isn't
    trustworthy enough to bootstrap an account from."""
    provider = BuiltinOAuthUserProvider()
    with pytest.raises(OAuthEmailNotVerifiedError):
        asyncio.run(
            provider.find_or_create_by_external_identity(
                provider="google",
                provider_subject="unverified-sub",
                claims=_claims(email_verified=False),
                allow_create=True,
                request=_fake_request(app),
            )
        )


def test_auto_create_refuses_missing_email(app):
    """Email-less claims — refuse rather than create an account that
    can't receive password resets."""
    provider = BuiltinOAuthUserProvider()
    with pytest.raises(OAuthEmailNotVerifiedError):
        asyncio.run(
            provider.find_or_create_by_external_identity(
                provider="google",
                provider_subject="no-email-sub",
                claims=_claims(email=None),
                allow_create=True,
                request=_fake_request(app),
            )
        )


def test_auto_create_refuses_email_already_taken(app):
    """The attack: attacker registers a Google account with a victim's
    email. Without this guard, the OAuth flow would auto-link the new
    identity to the existing User row, granting the attacker access.

    Refuse silent linking — the safe fallback is 'no login', not 'log
    me in as whoever owns this email'."""
    runtime = app.state.asterion
    factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)

    async def _seed():
        async with factory() as s, s.begin():
            s.add(
                User(
                    email="victim@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
                )
            )

    asyncio.run(_seed())

    provider = BuiltinOAuthUserProvider()
    with pytest.raises(OAuthEmailCollisionError):
        asyncio.run(
            provider.find_or_create_by_external_identity(
                provider="google",
                provider_subject="attacker-sub",
                claims=_claims(email="victim@example.com"),
                allow_create=True,
                request=_fake_request(app),
            )
        )


def test_auto_create_disabled_path_does_not_check_email(app):
    """When allow_create=False, we never reach the email checks —
    confirmed by passing claims that would fail every auto-create
    safety check, and still getting AutoCreateDisabled (not the
    more specific errors)."""
    provider = BuiltinOAuthUserProvider()
    with pytest.raises(OAuthAutoCreateDisabledError):
        asyncio.run(
            provider.find_or_create_by_external_identity(
                provider="google",
                provider_subject="never-seen",
                claims=_claims(email_verified=False, email=None),
                allow_create=False,
                request=_fake_request(app),
            )
        )


# --- extension wiring ---


def test_extension_defaults_to_builtin_oauth_user_provider():
    ext = OAuthExtension(providers=[GoogleOIDCProvider(client_id="x", client_secret="y")])
    assert isinstance(ext.user_provider, BuiltinOAuthUserProvider)
    assert ext.auto_create_users is False  # safe default


def test_extension_accepts_custom_oauth_user_provider():
    class _Custom:
        async def find_or_create_by_external_identity(self, **kwargs):
            raise NotImplementedError

    custom = _Custom()
    ext = OAuthExtension(
        providers=[GoogleOIDCProvider(client_id="x", client_secret="y")],
        user_provider=custom,
        auto_create_users=True,
    )
    assert ext.user_provider is custom
    assert ext.auto_create_users is True
