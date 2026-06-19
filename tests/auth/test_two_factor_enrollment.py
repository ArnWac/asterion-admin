"""2FA enrollment endpoints (Roadmap 3.4a) — setup / enable / disable.

The login step-up (demanding a code at sign-in) is 3.4b and tested
separately. These tests cover enrollment lifecycle only.
"""

from __future__ import annotations

import asyncio

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.auth.password import hash_password
from asterion.auth.tokens import create_access_token
from asterion.models.base import GlobalModel
from asterion.models.two_factor_backup_code import TwoFactorBackupCode
from asterion.models.user import User

SECRET = "test-2fa-secret"
ALG = "HS256"


@pytest.fixture
def app_with_user(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / '2fa.db'}"
    app = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key=SECRET,
            app_title="TestAdmin",
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
                u = User(
                    email="alice@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
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


def _token(user: User) -> str:
    return create_access_token(
        user.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=10,
        token_version=user.token_version,
    )


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user)}"}


def _read_user(runtime, user_id) -> User:
    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            return await session.get(User, user_id)

    return asyncio.run(_go())


def _count_backup_codes(runtime, user_id) -> int:
    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(TwoFactorBackupCode).where(TwoFactorBackupCode.user_id == user_id)
                    )
                )
                .scalars()
                .all()
            )
            return len(rows)

    return asyncio.run(_go())


# ---------------------------------------------------------------------------
# auth gate
# ---------------------------------------------------------------------------


def test_setup_requires_auth(app_with_user):
    app, _, _ = app_with_user
    assert _client(app).post("/api/v1/auth/2fa/setup").status_code == 401


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


def test_setup_returns_secret_and_uri(app_with_user):
    app, runtime, user = app_with_user
    resp = _client(app).post("/api/v1/auth/2fa/setup", headers=_auth(user))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["secret"]
    assert body["provisioning_uri"].startswith("otpauth://totp/")
    assert "TestAdmin" in body["provisioning_uri"]
    # Secret persisted as pending, but 2FA not yet enabled.
    refreshed = _read_user(runtime, user.id)
    assert refreshed.totp_secret == body["secret"]
    assert refreshed.totp_enabled is False


def test_setup_rejected_when_already_enabled(app_with_user):
    app, runtime, user = app_with_user

    # Force-enable in DB.
    async def _enable():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                u = await session.get(User, user.id)
                u.totp_secret = pyotp.random_base32()
                u.totp_enabled = True

    asyncio.run(_enable())
    resp = _client(app).post("/api/v1/auth/2fa/setup", headers=_auth(user))
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# enable
# ---------------------------------------------------------------------------


def test_enable_with_valid_code_activates_and_returns_backup_codes(app_with_user):
    app, runtime, user = app_with_user
    secret = _client(app).post("/api/v1/auth/2fa/setup", headers=_auth(user)).json()["secret"]

    code = pyotp.TOTP(secret).now()
    resp = _client(app).post("/api/v1/auth/2fa/enable", headers=_auth(user), json={"code": code})
    assert resp.status_code == 200, resp.text
    codes = resp.json()["backup_codes"]
    assert len(codes) == 10
    assert all("-" in c for c in codes)

    refreshed = _read_user(runtime, user.id)
    assert refreshed.totp_enabled is True
    assert _count_backup_codes(runtime, user.id) == 10


def test_enable_rejects_wrong_code(app_with_user):
    app, _, user = app_with_user
    _client(app).post("/api/v1/auth/2fa/setup", headers=_auth(user))
    resp = _client(app).post(
        "/api/v1/auth/2fa/enable", headers=_auth(user), json={"code": "000000"}
    )
    assert resp.status_code == 400


def test_enable_without_setup_is_400(app_with_user):
    app, _, user = app_with_user
    resp = _client(app).post(
        "/api/v1/auth/2fa/enable", headers=_auth(user), json={"code": "123456"}
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# disable
# ---------------------------------------------------------------------------


def _enroll(app, user) -> str:
    """Setup + enable, return the secret."""
    secret = _client(app).post("/api/v1/auth/2fa/setup", headers=_auth(user)).json()["secret"]
    code = pyotp.TOTP(secret).now()
    _client(app).post("/api/v1/auth/2fa/enable", headers=_auth(user), json={"code": code})
    return secret


def test_disable_with_valid_code_clears_2fa(app_with_user):
    app, runtime, user = app_with_user
    secret = _enroll(app, user)

    code = pyotp.TOTP(secret).now()
    resp = _client(app).post("/api/v1/auth/2fa/disable", headers=_auth(user), json={"code": code})
    assert resp.status_code == 200

    refreshed = _read_user(runtime, user.id)
    assert refreshed.totp_enabled is False
    assert refreshed.totp_secret is None
    assert _count_backup_codes(runtime, user.id) == 0


def test_disable_rejects_wrong_code(app_with_user):
    app, _, user = app_with_user
    _enroll(app, user)
    resp = _client(app).post(
        "/api/v1/auth/2fa/disable", headers=_auth(user), json={"code": "000000"}
    )
    assert resp.status_code == 400


def test_disable_when_not_enabled_is_400(app_with_user):
    app, _, user = app_with_user
    resp = _client(app).post(
        "/api/v1/auth/2fa/disable", headers=_auth(user), json={"code": "123456"}
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# helper-level: backup code consume
# ---------------------------------------------------------------------------


def test_backup_codes_are_hashed_not_plaintext(app_with_user):
    """The DB must store only hashes — a leaked snapshot can't be used
    as backup codes."""
    app, runtime, user = app_with_user
    plaintext = _client(app).post("/api/v1/auth/2fa/setup", headers=_auth(user)).json()["secret"]
    code = pyotp.TOTP(plaintext).now()
    backup = (
        _client(app)
        .post("/api/v1/auth/2fa/enable", headers=_auth(user), json={"code": code})
        .json()["backup_codes"]
    )

    async def _stored_hashes():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(TwoFactorBackupCode.code_hash).where(
                            TwoFactorBackupCode.user_id == user.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return set(rows)

    hashes = asyncio.run(_stored_hashes())
    # No plaintext backup code appears verbatim in the stored hashes.
    assert not (set(backup) & hashes)
