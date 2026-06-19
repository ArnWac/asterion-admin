"""POST /api/v1/auth/refresh — refresh-token rotation (Roadmap 3.1)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.auth.password import hash_password
from asterion.auth.tokens import (
    create_refresh_token,
    decode_token,
)
from asterion.models.base import GlobalModel
from asterion.models.revoked_token import RevokedToken
from asterion.models.user import User

SECRET = "test-refresh-secret"
ALG = "HS256"


@pytest.fixture
def app_with_user(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'refresh.db'}"
    app = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )
    runtime = app.state.asterion

    async def _setup() -> User:
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                user = User(
                    email="alice@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
                    token_version=0,
                )
                session.add(user)
            await session.refresh(user)
            return user

    user = asyncio.run(_setup())
    yield app, runtime, user
    asyncio.run(runtime.db.dispose())


def _client(app):
    return TestClient(app, raise_server_exceptions=False)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(app) -> dict:
    resp = _client(app).post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "hunter2-strong"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# login now returns a token pair
# ---------------------------------------------------------------------------


def test_login_returns_access_and_refresh(app_with_user):
    app, _, _ = app_with_user
    body = _login(app)
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["access_token"] != body["refresh_token"]


def test_login_access_and_refresh_have_distinct_types(app_with_user):
    app, _, _ = app_with_user
    body = _login(app)
    access = decode_token(body["access_token"], secret_key=SECRET, algorithm=ALG)
    refresh = decode_token(body["refresh_token"], secret_key=SECRET, algorithm=ALG)
    assert access["type"] == "access"
    assert refresh["type"] == "refresh"


# ---------------------------------------------------------------------------
# refresh exchange
# ---------------------------------------------------------------------------


def test_refresh_returns_new_pair(app_with_user):
    app, _, _ = app_with_user
    body = _login(app)
    resp = _client(app).post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert resp.status_code == 200, resp.text
    new = resp.json()
    assert new["access_token"]
    assert new["refresh_token"]
    # New tokens differ from the originals (fresh jti / iat).
    assert new["access_token"] != body["access_token"]
    assert new["refresh_token"] != body["refresh_token"]


def test_refreshed_access_token_authenticates(app_with_user):
    app, _, _ = app_with_user
    body = _login(app)
    new = (
        _client(app)
        .post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
        .json()
    )
    me = _client(app).get("/api/v1/auth/me", headers=_bearer(new["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


def test_old_refresh_token_rejected_after_rotation(app_with_user):
    """Rotation: the presented refresh token is single-use. Replaying
    it after a successful refresh must 401 (its jti is revoked)."""
    app, _, _ = app_with_user
    body = _login(app)
    first = _client(app).post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert first.status_code == 200
    replay = _client(app).post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert replay.status_code == 401


def test_rotation_writes_revoked_row(app_with_user):
    app, runtime, _ = app_with_user
    body = _login(app)
    _client(app).post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})

    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            rows = (await session.execute(select(RevokedToken))).scalars().all()
            return list(rows)

    rows = asyncio.run(_go())
    assert len(rows) == 1
    assert rows[0].reason == "refresh_rotation"


# ---------------------------------------------------------------------------
# rejections
# ---------------------------------------------------------------------------


def test_refresh_rejects_access_token(app_with_user):
    """An access token presented at /refresh must be rejected — only
    type=refresh is accepted."""
    app, _, _ = app_with_user
    body = _login(app)
    resp = _client(app).post("/api/v1/auth/refresh", json={"refresh_token": body["access_token"]})
    assert resp.status_code == 401


def test_refresh_rejects_garbage(app_with_user):
    app, _, _ = app_with_user
    resp = _client(app).post("/api/v1/auth/refresh", json={"refresh_token": "not-a-jwt"})
    assert resp.status_code == 401


def test_refresh_rejected_after_logout_all(app_with_user):
    """logout-all bumps token_version; a refresh token issued before
    that no longer matches and must be rejected (tkv invariant applies
    to refresh tokens too)."""
    app, _, user = app_with_user
    body = _login(app)
    # bump tkv via logout-all using the access token
    assert (
        _client(app)
        .post("/api/v1/auth/logout-all", headers=_bearer(body["access_token"]))
        .status_code
        == 200
    )
    resp = _client(app).post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert resp.status_code == 401


def test_refresh_rejected_for_inactive_user(app_with_user):
    app, runtime, user = app_with_user
    body = _login(app)

    async def _deactivate():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                fresh = await session.get(User, user.id)
                fresh.is_active = False

    asyncio.run(_deactivate())
    resp = _client(app).post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert resp.status_code == 401


def test_manually_minted_refresh_token_works(app_with_user):
    """Sanity that create_refresh_token + the endpoint agree (not just
    the login-issued one)."""
    app, _, user = app_with_user
    rt = create_refresh_token(
        user.id, secret_key=SECRET, algorithm=ALG, expires_minutes=60, token_version=0
    )
    resp = _client(app).post("/api/v1/auth/refresh", json={"refresh_token": rt})
    assert resp.status_code == 200
    assert resp.json()["access_token"]
