"""Password-reset flow (Roadmap 3.3).

POST /auth/password-reset/request  → 202 always (anti-enumeration)
POST /auth/password-reset/confirm  → set new password + invalidate sessions
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.auth.password import hash_password, verify_password
from asterion.auth.tokens import create_access_token
from asterion.models.base import GlobalModel
from asterion.models.password_reset_token import PasswordResetToken
from asterion.models.user import User

SECRET = "test-password-reset-secret"
ALG = "HS256"


class _CapturingNotifier:
    """Test notifier that records the raw token instead of emailing it."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_reset(self, *, email, token, request=None):
        self.sent.append({"email": email, "token": token})


@pytest.fixture
def app_with_user(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'pwreset.db'}"
    notifier = _CapturingNotifier()
    app = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
            password_min_length=8,
        ),
        password_reset_notifier=notifier,
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
                    hashed_password=hash_password("old-password-123"),
                    is_active=True,
                    token_version=0,
                )
                session.add(user)
            await session.refresh(user)
            return user

    user = asyncio.run(_setup())
    yield app, runtime, user, notifier
    asyncio.run(runtime.db.dispose())


def _client(app):
    return TestClient(app, raise_server_exceptions=False)


def _request_reset(app, email: str):
    return _client(app).post("/api/v1/auth/password-reset/request", json={"email": email})


def _read_user(runtime, user_id) -> User:
    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            return await session.get(User, user_id)

    return asyncio.run(_go())


def _count(runtime, model) -> int:
    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            return len((await session.execute(select(model))).scalars().all())

    return asyncio.run(_go())


# ---------------------------------------------------------------------------
# request — anti-enumeration
# ---------------------------------------------------------------------------


def test_request_known_user_returns_202_and_notifies(app_with_user):
    app, runtime, _, notifier = app_with_user
    resp = _request_reset(app, "alice@example.com")
    assert resp.status_code == 202
    assert len(notifier.sent) == 1
    assert notifier.sent[0]["email"] == "alice@example.com"
    assert notifier.sent[0]["token"]
    assert _count(runtime, PasswordResetToken) == 1


def test_request_unknown_email_returns_202_no_notify(app_with_user):
    """Same 202 + same body as the known-user path, but no token is
    issued and the notifier is not called — no enumeration signal."""
    app, runtime, _, notifier = app_with_user
    resp = _request_reset(app, "ghost@example.com")
    assert resp.status_code == 202
    assert notifier.sent == []
    assert _count(runtime, PasswordResetToken) == 0


def test_request_responses_are_identical(app_with_user):
    app, _, _, _ = app_with_user
    known = _request_reset(app, "alice@example.com")
    unknown = _request_reset(app, "ghost@example.com")
    assert known.status_code == unknown.status_code
    assert known.json() == unknown.json()


def test_request_inactive_user_does_not_issue(app_with_user):
    app, runtime, user, notifier = app_with_user

    async def _deactivate():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                fresh = await session.get(User, user.id)
                fresh.is_active = False

    asyncio.run(_deactivate())
    resp = _request_reset(app, "alice@example.com")
    assert resp.status_code == 202
    assert notifier.sent == []


# ---------------------------------------------------------------------------
# confirm
# ---------------------------------------------------------------------------


def test_confirm_sets_new_password(app_with_user):
    app, runtime, user, notifier = app_with_user
    _request_reset(app, "alice@example.com")
    token = notifier.sent[0]["token"]

    resp = _client(app).post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "brand-new-secret"},
    )
    assert resp.status_code == 200

    refreshed = _read_user(runtime, user.id)
    assert verify_password("brand-new-secret", refreshed.hashed_password)
    assert not verify_password("old-password-123", refreshed.hashed_password)


def test_confirm_bumps_token_version(app_with_user):
    app, runtime, user, notifier = app_with_user
    original = user.token_version
    _request_reset(app, "alice@example.com")
    token = notifier.sent[0]["token"]
    _client(app).post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "brand-new-secret"},
    )
    assert _read_user(runtime, user.id).token_version == original + 1


def test_confirm_invalidates_existing_sessions(app_with_user):
    """A token issued before the reset stops working afterward (tkv
    bump). Proves the 'reset implies compromise → kill sessions'
    behaviour."""
    app, runtime, user, notifier = app_with_user
    stale = create_access_token(
        user.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=10,
        token_version=user.token_version,
    )
    # token works before reset
    assert (
        _client(app)
        .get("/api/v1/auth/me", headers={"Authorization": f"Bearer {stale}"})
        .status_code
        == 200
    )

    _request_reset(app, "alice@example.com")
    token = notifier.sent[0]["token"]
    _client(app).post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "brand-new-secret"},
    )

    # stale token now rejected
    assert (
        _client(app)
        .get("/api/v1/auth/me", headers={"Authorization": f"Bearer {stale}"})
        .status_code
        == 401
    )


def test_confirm_token_is_single_use(app_with_user):
    app, _, _, notifier = app_with_user
    _request_reset(app, "alice@example.com")
    token = notifier.sent[0]["token"]
    first = _client(app).post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "brand-new-secret"},
    )
    assert first.status_code == 200
    second = _client(app).post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "another-secret"},
    )
    assert second.status_code == 400


def test_confirm_rejects_unknown_token(app_with_user):
    app, _, _, _ = app_with_user
    resp = _client(app).post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "not-a-real-token", "new_password": "brand-new-secret"},
    )
    assert resp.status_code == 400


def test_confirm_enforces_password_strength(app_with_user):
    app, _, _, notifier = app_with_user
    _request_reset(app, "alice@example.com")
    token = notifier.sent[0]["token"]
    resp = _client(app).post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "short"},
    )
    assert resp.status_code == 422


def test_login_works_with_new_password(app_with_user):
    app, _, _, notifier = app_with_user
    _request_reset(app, "alice@example.com")
    token = notifier.sent[0]["token"]
    _client(app).post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "brand-new-secret"},
    )
    resp = _client(app).post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "brand-new-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]


# ---------------------------------------------------------------------------
# default notifier
# ---------------------------------------------------------------------------


def test_default_notifier_is_logging(tmp_path):
    """Without an explicit notifier, create_admin wires the dev-only
    logging notifier — the flow still works (token issued), it's just
    logged instead of emailed."""
    from asterion.auth.password_reset import LoggingPasswordResetNotifier

    app = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'def.db'}",
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )
    assert isinstance(app.state.asterion.password_reset_notifier, LoggingPasswordResetNotifier)
