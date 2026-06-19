"""POST /api/v1/auth/logout-all (plan §PR-8)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.audit import LOGOUT_ALL
from asterion.auth.password import hash_password
from asterion.auth.tokens import create_access_token
from asterion.models.audit_log import AuditLog
from asterion.models.base import GlobalModel
from asterion.models.user import User

SECRET = "test-logout-all-secret"
ALG = "HS256"


@pytest.fixture
def app_with_user(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'logout.db'}"
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
                    token_version=3,
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


def _issue_token(user: User) -> str:
    return create_access_token(
        user.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=5,
        token_version=user.token_version,
    )


def _read_user(runtime, user_id) -> User:
    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            return await session.get(User, user_id)

    return asyncio.run(_go())


def _audit_rows(runtime, action: str) -> list[AuditLog]:
    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            result = await session.execute(select(AuditLog).where(AuditLog.action == action))
            return list(result.scalars().all())

    return asyncio.run(_go())


# --- auth ---


def test_logout_all_requires_authentication(app_with_user):
    app, _, _ = app_with_user
    resp = _client(app).post("/api/v1/auth/logout-all")
    assert resp.status_code == 401


def test_logout_all_rejects_invalid_token(app_with_user):
    app, _, _ = app_with_user
    resp = _client(app).post("/api/v1/auth/logout-all", headers=_bearer("nope"))
    assert resp.status_code == 401


# --- happy path ---


def test_logout_all_bumps_token_version(app_with_user):
    app, runtime, user = app_with_user
    original_tkv = user.token_version
    token = _issue_token(user)

    resp = _client(app).post("/api/v1/auth/logout-all", headers=_bearer(token))
    assert resp.status_code == 200
    assert resp.json() == {"detail": "All sessions invalidated."}

    refreshed = _read_user(runtime, user.id)
    assert refreshed.token_version == original_tkv + 1


def test_previously_issued_token_rejected_after_logout_all(app_with_user):
    app, runtime, user = app_with_user
    token = _issue_token(user)
    # First request should succeed
    assert _client(app).post("/api/v1/auth/logout-all", headers=_bearer(token)).status_code == 200
    # Same token, second request — tkv mismatch -> 401
    resp = _client(app).get("/api/v1/auth/me", headers=_bearer(token))
    assert resp.status_code == 401


def test_freshly_issued_token_works_after_logout_all(app_with_user):
    app, runtime, user = app_with_user
    stale_token = _issue_token(user)
    _client(app).post("/api/v1/auth/logout-all", headers=_bearer(stale_token))

    fresh_user = _read_user(runtime, user.id)
    fresh_token = create_access_token(
        fresh_user.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=5,
        token_version=fresh_user.token_version,
    )
    resp = _client(app).get("/api/v1/auth/me", headers=_bearer(fresh_token))
    assert resp.status_code == 200
    assert resp.json()["email"] == "alice@example.com"


# --- audit ---


def test_logout_all_writes_audit_row(app_with_user):
    app, runtime, user = app_with_user
    token = _issue_token(user)
    _client(app).post("/api/v1/auth/logout-all", headers=_bearer(token))

    rows = _audit_rows(runtime, LOGOUT_ALL)
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_user_id == user.id
    assert row.path == "/api/v1/auth/logout-all"
    assert row.status_code == 200
    assert row.changes["bumped_to"] == user.token_version + 1
