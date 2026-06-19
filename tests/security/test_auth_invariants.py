"""Tests for token_version revocation and impersonation invariants."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion.auth.dependencies import get_current_user, require_superadmin
from asterion.auth.password import hash_password
from asterion.auth.rate_limiter import InMemoryLoginRateLimiter
from asterion.auth.tokens import (
    ACCESS_TOKEN_TYPE,
    IMPERSONATION_TOKEN_TYPE,
    TokenError,
    create_access_token,
    create_impersonation_token,
    decode_access_token,
    get_subject_user_id,
    get_token_version,
    is_impersonation_token,
)
from asterion.core.config import CoreAdminConfig
from asterion.core.runtime import AdminRuntime
from asterion.db.session import DatabaseManager
from asterion.models.base import GlobalModel
from asterion.models.user import User

SECRET = "test-secret-key-for-auth-invariants"
ALG = "HS256"


# --- pure token decode invariants ---


def test_decode_rejects_wrong_secret():
    token = create_access_token(
        "11111111-1111-1111-1111-111111111111",
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=5,
    )
    with pytest.raises(TokenError):
        decode_access_token(token, secret_key="other-secret", algorithm=ALG)


def test_decode_rejects_when_impersonation_disallowed():
    token = create_impersonation_token(
        "11111111-1111-1111-1111-111111111111",
        impersonated_by_user_id="22222222-2222-2222-2222-222222222222",
        tenant_id=None,
        secret_key=SECRET,
        algorithm=ALG,
    )
    with pytest.raises(TokenError):
        decode_access_token(token, secret_key=SECRET, algorithm=ALG, allow_impersonation=False)


def test_decoded_payload_carries_required_claims():
    token = create_access_token(
        "11111111-1111-1111-1111-111111111111",
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=5,
        token_version=7,
    )
    payload = decode_access_token(token, secret_key=SECRET, algorithm=ALG)
    assert payload["type"] == ACCESS_TOKEN_TYPE
    assert payload["tkv"] == 7
    assert payload["sub"]
    assert payload["jti"]
    assert payload["exp"]
    assert get_token_version(payload) == 7
    assert get_subject_user_id(payload)


def test_impersonation_token_distinct_from_access_token():
    impersonation = create_impersonation_token(
        "11111111-1111-1111-1111-111111111111",
        impersonated_by_user_id="22222222-2222-2222-2222-222222222222",
        tenant_id=None,
        secret_key=SECRET,
        algorithm=ALG,
    )
    payload = decode_access_token(impersonation, secret_key=SECRET, algorithm=ALG)
    assert payload["type"] == IMPERSONATION_TOKEN_TYPE
    assert is_impersonation_token(payload)


# --- get_current_user invariants via TestClient ---


@pytest.fixture
def app_with_user(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    config = CoreAdminConfig(
        database_url=db_url,
        secret_key=SECRET,
        enable_multi_tenant=False,
        enable_builtin_ui=False,
        enable_builtin_admins=False,
    )

    app = FastAPI()
    runtime = AdminRuntime(
        config=config,
        db=DatabaseManager(db_url),
    )
    app.state.asterion = runtime

    async def _setup_schema_and_user() -> User:
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                user = User(
                    email="alice@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
                    is_superadmin=False,
                    token_version=0,
                )
                session.add(user)
            await session.refresh(user)
            return user

    user = asyncio.run(_setup_schema_and_user())

    @app.get("/protected")
    async def protected(current=__import__("fastapi").Depends(get_current_user)):
        return {"id": str(current.id), "email": current.email}

    @app.get("/superadmin-only")
    async def superadmin_only(
        current=__import__("fastapi").Depends(require_superadmin),
    ):
        return {"id": str(current.id)}

    yield app, runtime, user

    asyncio.run(runtime.db.dispose())


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_get_current_user_rejects_missing_credentials(app_with_user):
    app, _, _ = app_with_user
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/protected")
    assert resp.status_code == 401


def test_get_current_user_rejects_invalid_token(app_with_user):
    app, _, _ = app_with_user
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/protected", headers=_auth_headers("not-a-jwt"))
    assert resp.status_code == 401


def test_get_current_user_accepts_valid_token(app_with_user):
    app, _, user = app_with_user
    token = create_access_token(
        user.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=5,
        token_version=user.token_version,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/protected", headers=_auth_headers(token))
    assert resp.status_code == 200


def test_get_current_user_rejects_stale_token_version(app_with_user):
    app, runtime, user = app_with_user
    token = create_access_token(
        user.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=5,
        token_version=user.token_version,
    )

    async def _bump_token_version():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                fresh = await session.get(User, user.id)
                fresh.token_version += 1

    asyncio.run(_bump_token_version())

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/protected", headers=_auth_headers(token))
    assert resp.status_code == 401


def test_get_current_user_rejects_inactive_user(app_with_user):
    app, runtime, user = app_with_user
    token = create_access_token(
        user.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=5,
        token_version=user.token_version,
    )

    async def _deactivate():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                fresh = await session.get(User, user.id)
                fresh.is_active = False

    asyncio.run(_deactivate())

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/protected", headers=_auth_headers(token))
    assert resp.status_code == 403


def test_require_superadmin_rejects_impersonation_token(app_with_user):
    app, runtime, user = app_with_user

    async def _make_super():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                fresh = await session.get(User, user.id)
                fresh.is_superadmin = True

    asyncio.run(_make_super())

    impersonation = create_impersonation_token(
        user.id,
        impersonated_by_user_id=user.id,
        tenant_id=None,
        secret_key=SECRET,
        algorithm=ALG,
        token_version=user.token_version,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/superadmin-only", headers=_auth_headers(impersonation))
    assert resp.status_code == 403


def test_require_superadmin_accepts_normal_superadmin(app_with_user):
    app, runtime, user = app_with_user

    async def _make_super():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                fresh = await session.get(User, user.id)
                fresh.is_superadmin = True

    asyncio.run(_make_super())

    token = create_access_token(
        user.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=5,
        token_version=user.token_version,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/superadmin-only", headers=_auth_headers(token))
    assert resp.status_code == 200


# --- rate limiter (MVP-S4 + PR-9 async API) ---


@pytest.mark.asyncio
async def test_rate_limiter_blocks_after_threshold():
    limiter = InMemoryLoginRateLimiter(max_failures=3, window_seconds=60)
    for _ in range(3):
        await limiter.record_failure("user@example.com")
    assert await limiter.is_limited("user@example.com") is True


@pytest.mark.asyncio
async def test_rate_limiter_does_not_block_under_threshold():
    limiter = InMemoryLoginRateLimiter(max_failures=3, window_seconds=60)
    for _ in range(2):
        await limiter.record_failure("user@example.com")
    assert await limiter.is_limited("user@example.com") is False


@pytest.mark.asyncio
async def test_rate_limiter_clear_resets_user():
    limiter = InMemoryLoginRateLimiter(max_failures=2, window_seconds=60)
    await limiter.record_failure("user@example.com")
    await limiter.record_failure("user@example.com")
    assert await limiter.is_limited("user@example.com") is True
    await limiter.clear("user@example.com")
    assert await limiter.is_limited("user@example.com") is False


@pytest.mark.asyncio
async def test_rate_limiter_reset_clears_all_keys():
    limiter = InMemoryLoginRateLimiter(max_failures=1, window_seconds=60)
    await limiter.record_failure("a@example.com")
    await limiter.record_failure("b@example.com")
    assert await limiter.is_limited("a@example.com") is True
    limiter.reset()  # sync helper for tests
    assert await limiter.is_limited("a@example.com") is False
    assert await limiter.is_limited("b@example.com") is False
