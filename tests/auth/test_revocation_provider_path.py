"""Roadmap 3.2 — jti revocation is enforced on the PROVIDER auth path too.

``/auth/me`` + ``/auth/logout`` go through the ``get_current_user``
dependency (covered in test_logout_single.py). CRUD / contract / action
routes go through ``BuiltinJWTAuthProvider.authenticate_request`` via
``require_admin_context``. Both paths must reject a revoked token —
this is the documented two-path risk, so it gets its own test against
the provider path specifically.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.auth.password import hash_password
from asterion.auth.tokens import create_access_token, decode_token, get_token_jti
from asterion.models.base import GlobalModel
from asterion.models.user import User

SECRET = "test-revocation-provider-secret"
ALG = "HS256"


class _Base(DeclarativeBase):
    pass


class _Widget(_Base):
    __tablename__ = "rev_widgets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)


class _WidgetAdmin(ModelAdmin):
    model = _Widget
    list_display = ["id", "name"]


@pytest.fixture
def app_with_user(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'revp.db'}"
    app = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key=SECRET,
            enable_multi_tenant=False,  # no tenant → CRUD permission gate is a no-op
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: reg.register(_WidgetAdmin),
    )
    runtime = app.state.asterion

    async def _setup() -> User:
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
            await conn.run_sync(_Widget.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                u = User(
                    email="alice@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
                    is_superadmin=True,
                    token_version=0,
                )
                session.add(u)
            await session.refresh(u)
            return u

    user = asyncio.run(_setup())
    yield app, runtime, user
    asyncio.run(runtime.db.dispose())


def _client(app):
    return TestClient(app, raise_server_exceptions=False)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _issue(user: User) -> str:
    return create_access_token(
        user.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=5,
        token_version=user.token_version,
    )


def test_crud_route_works_before_revocation(app_with_user):
    """Sanity: the CRUD list route (provider auth path) accepts the
    token before it's revoked."""
    app, _, user = app_with_user
    token = _issue(user)
    resp = _client(app).get("/api/v1/admin/rev_widgets", headers=_bearer(token))
    assert resp.status_code == 200, resp.text


def test_crud_route_rejects_revoked_token(app_with_user):
    """Revoke the token via /auth/logout (dependency path writes the
    row), then hit a CRUD route (provider path reads it) — must 401.
    Proves both paths share the revocation store."""
    app, _, user = app_with_user
    token = _issue(user)

    # Revoke via the single-logout endpoint.
    assert _client(app).post("/api/v1/auth/logout", headers=_bearer(token)).status_code == 200

    # Provider path (CRUD list) must now reject the same token.
    resp = _client(app).get("/api/v1/admin/rev_widgets", headers=_bearer(token))
    assert resp.status_code == 401


def test_distinct_token_unaffected_on_provider_path(app_with_user):
    """A second token for the same user keeps working on the provider
    path after the first is revoked — single-token semantics hold on
    both paths."""
    app, _, user = app_with_user
    token_a = _issue(user)
    token_b = _issue(user)

    # Confirm the two tokens really differ by jti (otherwise the test
    # is vacuous).
    jti_a = get_token_jti(decode_token(token_a, secret_key=SECRET, algorithm=ALG))
    jti_b = get_token_jti(decode_token(token_b, secret_key=SECRET, algorithm=ALG))
    assert jti_a != jti_b

    _client(app).post("/api/v1/auth/logout", headers=_bearer(token_a))

    assert (
        _client(app).get("/api/v1/admin/rev_widgets", headers=_bearer(token_a)).status_code == 401
    )
    assert (
        _client(app).get("/api/v1/admin/rev_widgets", headers=_bearer(token_b)).status_code == 200
    )
