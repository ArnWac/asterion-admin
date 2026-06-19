"""POST /api/v1/auth/logout — single-token revocation (Roadmap 3.2)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.audit import LOGOUT
from asterion.auth.password import hash_password
from asterion.auth.tokens import create_access_token
from asterion.models.audit_log import AuditLog
from asterion.models.base import GlobalModel
from asterion.models.revoked_token import RevokedToken
from asterion.models.user import User

SECRET = "test-logout-single-secret"
ALG = "HS256"


@pytest.fixture
def app_with_user(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'logout1.db'}"
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


def _issue(user: User) -> str:
    return create_access_token(
        user.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=5,
        token_version=user.token_version,
    )


def _count(runtime, model) -> int:
    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            rows = (await session.execute(select(model))).scalars().all()
            return len(rows)

    return asyncio.run(_go())


# --- auth gate ---


def test_logout_requires_authentication(app_with_user):
    app, _, _ = app_with_user
    assert _client(app).post("/api/v1/auth/logout").status_code == 401


def test_logout_rejects_invalid_token(app_with_user):
    app, _, _ = app_with_user
    assert _client(app).post("/api/v1/auth/logout", headers=_bearer("nope")).status_code == 401


# --- single-token revocation ---


def test_logout_revokes_only_this_token(app_with_user):
    """Two tokens for the same user; logging out token A must revoke A
    but leave token B working. This is the difference from logout-all."""
    app, runtime, user = app_with_user
    token_a = _issue(user)
    token_b = _issue(user)

    # token_a logs itself out.
    resp = _client(app).post("/api/v1/auth/logout", headers=_bearer(token_a))
    assert resp.status_code == 200
    assert resp.json() == {"detail": "Session invalidated."}

    # token_a is now rejected...
    assert _client(app).get("/api/v1/auth/me", headers=_bearer(token_a)).status_code == 401
    # ...but token_b still works (single-token, not all-sessions).
    me_b = _client(app).get("/api/v1/auth/me", headers=_bearer(token_b))
    assert me_b.status_code == 200
    assert me_b.json()["email"] == "alice@example.com"


def test_logout_writes_revoked_token_row(app_with_user):
    app, runtime, user = app_with_user
    token = _issue(user)
    _client(app).post("/api/v1/auth/logout", headers=_bearer(token))
    assert _count(runtime, RevokedToken) == 1


def test_logout_is_idempotent(app_with_user):
    """Replaying logout with a still-valid second token whose jti was
    already revoked... actually each token has its own jti, so test
    idempotency by re-posting the SAME token — second call is rejected
    at the auth gate (token already revoked), proving no duplicate row
    and no 500."""
    app, runtime, user = app_with_user
    token = _issue(user)
    first = _client(app).post("/api/v1/auth/logout", headers=_bearer(token))
    assert first.status_code == 200
    # The token revoked itself — a second logout with it is now 401.
    second = _client(app).post("/api/v1/auth/logout", headers=_bearer(token))
    assert second.status_code == 401
    # Still exactly one revocation row.
    assert _count(runtime, RevokedToken) == 1


def test_logout_writes_audit_row(app_with_user):
    app, runtime, user = app_with_user
    token = _issue(user)
    _client(app).post("/api/v1/auth/logout", headers=_bearer(token))

    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            rows = (
                (await session.execute(select(AuditLog).where(AuditLog.action == LOGOUT)))
                .scalars()
                .all()
            )
            return list(rows)

    rows = asyncio.run(_go())
    assert len(rows) == 1
    assert rows[0].actor_user_id == user.id
    assert rows[0].path == "/api/v1/auth/logout"
    assert rows[0].changes["newly_revoked"] is True


def test_revoked_token_carries_expiry_and_reason(app_with_user):
    """The revocation row mirrors the token exp (for pruning) and tags
    the reason."""
    app, runtime, user = app_with_user
    token = _issue(user)
    _client(app).post("/api/v1/auth/logout", headers=_bearer(token))

    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            return (await session.execute(select(RevokedToken))).scalars().one()

    row = asyncio.run(_go())
    assert row.reason == "logout"
    assert row.expires_at is not None
    assert row.user_id == user.id


# --- interaction with logout-all ---


def test_logout_all_still_works_alongside_single_logout(app_with_user):
    """logout-all (token_version bump) and single logout (jti revoke)
    are independent mechanisms — both reject the token afterward."""
    app, runtime, user = app_with_user
    token = _issue(user)
    # logout-all bumps tkv → token rejected by tkv mismatch.
    assert _client(app).post("/api/v1/auth/logout-all", headers=_bearer(token)).status_code == 200
    assert _client(app).get("/api/v1/auth/me", headers=_bearer(token)).status_code == 401
