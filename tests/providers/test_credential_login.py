"""Roadmap 2.6 — CredentialAuthProvider.login.

Credential verification + token minting moved from auth/router.py into
``BuiltinJWTAuthProvider.login`` (the CredentialAuthProvider surface).
The route keeps rate-limiting + audit + HTTP mapping.

Covers:
* ``CredentialAuthProvider`` is a separate optional protocol — an
  auth-only provider (authenticate_request only) doesn't satisfy it.
* The builtin provider's login raises LoginError with the right reason
  for bad password / unknown email / inactive user, and returns an
  AuthSession on success.
* The /auth/login route still maps reasons to 401/403, rate-limits,
  and returns 501 when the provider can't do credential login.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.auth.password import hash_password
from asterion.models.base import GlobalModel
from asterion.models.user import User
from asterion.providers.auth import BuiltinJWTAuthProvider
from asterion.providers.base import (
    AuthProvider,
    AuthSession,
    CredentialAuthProvider,
    LoginCredentials,
    LoginError,
)

SECRET = "test-credential-login-secret"


# ---------------------------------------------------------------------------
# Protocol separation
# ---------------------------------------------------------------------------


class _AuthOnlyProvider:
    """Implements AuthProvider (authenticate_request) but not login."""

    async def authenticate_request(self, request):
        return None


def test_auth_only_provider_is_auth_provider():
    assert isinstance(_AuthOnlyProvider(), AuthProvider)


def test_auth_only_provider_is_not_credential_provider():
    assert not isinstance(_AuthOnlyProvider(), CredentialAuthProvider)


def test_builtin_provider_is_credential_provider():
    assert isinstance(BuiltinJWTAuthProvider(), CredentialAuthProvider)


# ---------------------------------------------------------------------------
# Builtin login behaviour — provider-level
# ---------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'cl.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )
    runtime = application.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add(
                    User(
                        email="active@example.com",
                        hashed_password=hash_password("correct-horse-battery"),
                        is_active=True,
                    )
                )
                session.add(
                    User(
                        email="inactive@example.com",
                        hashed_password=hash_password("correct-horse-battery"),
                        is_active=False,
                    )
                )

    asyncio.run(_setup())
    yield application
    asyncio.run(runtime.db.dispose())


class _FakeRequest:
    def __init__(self, application):
        self.app = application


def test_builtin_login_success_returns_session(app):
    provider = BuiltinJWTAuthProvider()
    session = asyncio.run(
        provider.login(
            LoginCredentials(email="active@example.com", password="correct-horse-battery"),
            request=_FakeRequest(app),
        )
    )
    assert isinstance(session, AuthSession)
    assert session.access_token
    assert session.token_type == "bearer"
    assert session.subject is not None
    assert session.expires_in == 60 * 60  # default 60 min → seconds


def test_builtin_login_wrong_password_raises_invalid_credentials(app):
    provider = BuiltinJWTAuthProvider()
    with pytest.raises(LoginError) as exc:
        asyncio.run(
            provider.login(
                LoginCredentials(email="active@example.com", password="wrong"),
                request=_FakeRequest(app),
            )
        )
    assert exc.value.reason == "invalid_credentials"


def test_builtin_login_unknown_email_raises_invalid_credentials(app):
    provider = BuiltinJWTAuthProvider()
    with pytest.raises(LoginError) as exc:
        asyncio.run(
            provider.login(
                LoginCredentials(email="ghost@example.com", password="whatever"),
                request=_FakeRequest(app),
            )
        )
    assert exc.value.reason == "invalid_credentials"


def test_builtin_login_inactive_raises_inactive_user(app):
    provider = BuiltinJWTAuthProvider()
    with pytest.raises(LoginError) as exc:
        asyncio.run(
            provider.login(
                LoginCredentials(email="inactive@example.com", password="correct-horse-battery"),
                request=_FakeRequest(app),
            )
        )
    assert exc.value.reason == "inactive_user"


# ---------------------------------------------------------------------------
# /auth/login route still behaves correctly through the provider
# ---------------------------------------------------------------------------


def test_route_login_success(app):
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "active@example.com", "password": "correct-horse-battery"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]
    assert resp.json()["token_type"] == "bearer"


def test_route_login_wrong_password_is_401(app):
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "active@example.com", "password": "nope"},
    )
    assert resp.status_code == 401


def test_route_login_inactive_is_403(app):
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "inactive@example.com", "password": "correct-horse-battery"},
    )
    assert resp.status_code == 403


def test_route_login_minted_token_authenticates(app):
    """The token from the provider login must work on an authenticated
    route — proves the provider's mint + the request-auth path agree."""
    client = TestClient(app, raise_server_exceptions=False)
    token = client.post(
        "/api/v1/auth/login",
        json={"email": "active@example.com", "password": "correct-horse-battery"},
    ).json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "active@example.com"


# ---------------------------------------------------------------------------
# Provider without login → 501
# ---------------------------------------------------------------------------


def test_route_login_501_when_provider_has_no_login(tmp_path):
    """An app wired with a pure request-auth provider (no login) makes
    /auth/login return 501, not 500."""

    class _ExternalAuth:
        async def authenticate_request(self, request):
            return None

    app = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'nologin.db'}",
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
            user_mode="external",
        ),
        auth_provider=_ExternalAuth(),
    )
    runtime = app.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)

    asyncio.run(_setup())
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "x@example.com", "password": "y"},
    )
    assert resp.status_code == 501
    asyncio.run(runtime.db.dispose())
