"""Roadmap 2.5 — UserProvider.list_users (root-panel listing).

Covers:
* ``UserListingProvider`` is a separate optional protocol — an
  auth-only provider (``get_by_id`` only) still satisfies
  ``UserProvider`` but NOT ``UserListingProvider``.
* The builtin provider's ``list_users`` returns ALL users including
  inactive ones (distinct from ``get_by_id`` which filters inactive).
* The root ``/users`` endpoint goes through the provider and returns
  501 when the configured provider can't list.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.auth.password import hash_password
from asterion.auth.tokens import create_access_token
from asterion.models.base import GlobalModel
from asterion.models.user import User
from asterion.providers.base import (
    AdminPrincipal,
    Page,
    UserListingProvider,
    UserProvider,
    UserQuery,
)
from asterion.providers.users import BuiltinSQLAlchemyUserProvider

SECRET = "test-user-listing-secret"


# ---------------------------------------------------------------------------
# Protocol separation
# ---------------------------------------------------------------------------


class _AuthOnlyProvider:
    """Implements UserProvider (get_by_id) but NOT list_users."""

    async def get_by_id(self, user_id, *, request=None):
        return AdminPrincipal(id=user_id)


class _ListingProvider:
    async def get_by_id(self, user_id, *, request=None):
        return AdminPrincipal(id=user_id)

    async def list_users(self, query, *, request=None):
        return Page(items=[], total=0, limit=query.limit, offset=query.offset)


def test_auth_only_provider_is_user_provider():
    assert isinstance(_AuthOnlyProvider(), UserProvider)


def test_auth_only_provider_is_not_listing_provider():
    """The whole reason list_users is a separate protocol: an auth-only
    provider must still pass UserProvider without being forced to
    implement listing."""
    assert not isinstance(_AuthOnlyProvider(), UserListingProvider)


def test_listing_provider_satisfies_both_protocols():
    p = _ListingProvider()
    assert isinstance(p, UserProvider)
    assert isinstance(p, UserListingProvider)


def test_builtin_provider_is_listing_provider():
    assert isinstance(BuiltinSQLAlchemyUserProvider(), UserListingProvider)


# ---------------------------------------------------------------------------
# Builtin list_users behaviour (incl. inactive) — via HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def app_state(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'ul.db'}"
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
    state: dict = {}

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                su = User(
                    email="root@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
                    is_superadmin=True,
                )
                active = User(
                    email="active@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    full_name="Active Annie",
                    is_active=True,
                )
                inactive = User(
                    email="inactive@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    full_name="Inactive Ivan",
                    is_active=False,
                )
                session.add_all([su, active, inactive])
            await session.refresh(su)
            state["superadmin"] = su

    asyncio.run(_setup())
    yield application, state
    asyncio.run(runtime.db.dispose())


def _su_headers(state) -> dict:
    su = state["superadmin"]
    token = create_access_token(
        su.id,
        secret_key=SECRET,
        algorithm="HS256",
        expires_minutes=10,
        token_version=su.token_version,
    )
    return {"Authorization": f"Bearer {token}"}


def test_root_users_list_includes_inactive(app_state):
    """The root panel must see inactive users (so an admin can
    re-activate them) — ``list_users`` deliberately does NOT apply the
    active filter that ``get_by_id`` uses for the auth path."""
    app, state = app_state
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/root/users", headers=_su_headers(state))
    assert resp.status_code == 200, resp.text
    emails = {u["email"] for u in resp.json()["items"]}
    assert "inactive@example.com" in emails
    assert "active@example.com" in emails
    # The inactive flag round-trips so the UI can show + toggle it.
    inactive = next(u for u in resp.json()["items"] if u["email"] == "inactive@example.com")
    assert inactive["is_active"] is False


def test_root_users_list_search_filters(app_state):
    app, state = app_state
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/root/users", params={"search": "Annie"}, headers=_su_headers(state))
    assert resp.status_code == 200
    emails = {u["email"] for u in resp.json()["items"]}
    assert emails == {"active@example.com"}


def test_root_users_list_pagination_envelope(app_state):
    app, state = app_state
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(
        "/api/v1/root/users", params={"limit": 2, "offset": 0}, headers=_su_headers(state)
    )
    body = resp.json()
    assert body["total"] == 3  # su + active + inactive
    assert body["limit"] == 2
    assert len(body["items"]) == 2


# ---------------------------------------------------------------------------
# External provider without listing → 501
# ---------------------------------------------------------------------------


def test_root_users_list_returns_501_when_provider_cannot_list(tmp_path):
    """An app wired with an auth-only external UserProvider must get a
    clear 501 from the root listing endpoint, not a 500."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'ul501.db'}"

    su_holder: dict = {}

    class _ExternalAuthOnly:
        async def get_by_id(self, user_id, *, request=None):
            # Return the seeded superadmin so require_superadmin passes.
            su = su_holder.get("su")
            if su is not None and str(su.id) == str(user_id):
                return AdminPrincipal(
                    id=str(su.id), email=su.email, is_active=True, is_superadmin=True
                )
            return None

    # external mode requires an auth_provider — provide a trivial one
    # that authenticates the seeded superadmin via the framework's JWT.
    class _ExternalAuth:
        async def authenticate_request(self, request):
            from asterion.auth.tokens import decode_access_token
            from asterion.providers.base import AuthIdentity

            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return None
            try:
                payload = decode_access_token(
                    header.removeprefix("Bearer "),
                    secret_key=SECRET,
                    algorithm="HS256",
                )
            except Exception:
                return None
            return AuthIdentity(user_id=payload["sub"])

    app = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
            user_mode="external",
        ),
        auth_provider=_ExternalAuth(),
        user_provider=_ExternalAuthOnly(),
    )
    runtime = app.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                su = User(
                    email="root@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
                    is_superadmin=True,
                )
                session.add(su)
            await session.refresh(su)
            su_holder["su"] = su

    asyncio.run(_setup())

    token = create_access_token(
        su_holder["su"].id,
        secret_key=SECRET,
        algorithm="HS256",
        expires_minutes=10,
        token_version=su_holder["su"].token_version,
    )
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/root/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 501
    asyncio.run(runtime.db.dispose())


# ---------------------------------------------------------------------------
# Provider-level unit test
# ---------------------------------------------------------------------------


def test_builtin_list_users_unit(app_state):
    """Call the provider directly (not via HTTP) to pin the Page shape."""
    app, _ = app_state
    provider = BuiltinSQLAlchemyUserProvider()

    # The provider needs a request to reach the DB; fake the minimal
    # surface it touches (request.app.state.asterion).
    class _FakeRequest:
        def __init__(self, application):
            self.app = application

    page = asyncio.run(
        provider.list_users(UserQuery(limit=10, offset=0), request=_FakeRequest(app))
    )
    assert isinstance(page, Page)
    assert page.total == 3
    assert {p.email for p in page.items} == {
        "root@example.com",
        "active@example.com",
        "inactive@example.com",
    }
