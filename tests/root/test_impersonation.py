"""HTTP integration tests for POST /api/v1/root/impersonate."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.audit import IMPERSONATION_START
from asterion.auth.password import hash_password
from asterion.auth.tokens import (
    create_access_token,
    create_impersonation_token,
    decode_access_token,
    is_impersonation_token,
)
from asterion.models.audit_log import AuditLog
from asterion.models.base import GlobalModel
from asterion.models.impersonation_log import ImpersonationLog
from asterion.models.user import User

SECRET = "test-impersonate-secret"
ALG = "HS256"


@pytest.fixture
def app_state(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'imp.db'}"
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

    state = {"superadmin": None, "user": None}

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                superadmin = User(
                    email="root@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
                    is_superadmin=True,
                )
                normal = User(
                    email="alice@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
                    is_superadmin=False,
                )
                session.add(superadmin)
                session.add(normal)
            await session.refresh(superadmin)
            await session.refresh(normal)
            state["superadmin"] = superadmin
            state["user"] = normal

    asyncio.run(_setup())

    yield application, state

    asyncio.run(runtime.db.dispose())


def _client(app):
    return TestClient(app, raise_server_exceptions=False)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _superadmin_token(state) -> str:
    su = state["superadmin"]
    return create_access_token(
        su.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=10,
        token_version=su.token_version,
    )


def _user_token(state) -> str:
    u = state["user"]
    return create_access_token(
        u.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=10,
        token_version=u.token_version,
    )


def _impersonation_logs(app) -> list[ImpersonationLog]:
    runtime = app.state.asterion

    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            result = await session.execute(select(ImpersonationLog))
            return list(result.scalars().all())

    return asyncio.run(_go())


def _audit_rows(app, action: str) -> list[AuditLog]:
    runtime = app.state.asterion

    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            result = await session.execute(select(AuditLog).where(AuditLog.action == action))
            return list(result.scalars().all())

    return asyncio.run(_go())


# --- auth ---


def test_unauthenticated_returns_401(app_state):
    app, _ = app_state
    resp = _client(app).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 401


def test_normal_user_cannot_impersonate(app_state):
    app, state = app_state
    resp = _client(app).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(state["user"].id)},
        headers=_bearer(_user_token(state)),
    )
    assert resp.status_code == 403


def test_impersonation_token_cannot_re_impersonate(app_state):
    app, state = app_state
    target = state["user"]
    superadmin = state["superadmin"]
    impersonation = create_impersonation_token(
        target.id,
        impersonated_by_user_id=superadmin.id,
        tenant_id=None,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=10,
        token_version=target.token_version,
    )
    resp = _client(app).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(uuid.uuid4())},
        headers=_bearer(impersonation),
    )
    assert resp.status_code == 403


# --- happy path ---


def test_superadmin_can_impersonate(app_state):
    app, state = app_state
    resp = _client(app).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(state["user"].id)},
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["target_user_id"] == str(state["user"].id)
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0


def test_minted_token_is_impersonation_type(app_state):
    app, state = app_state
    resp = _client(app).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(state["user"].id)},
        headers=_bearer(_superadmin_token(state)),
    )
    payload = decode_access_token(resp.json()["access_token"], secret_key=SECRET, algorithm=ALG)
    assert is_impersonation_token(payload)
    assert payload["sub"] == str(state["user"].id)
    assert payload["impersonated_by"] == str(state["superadmin"].id)


def test_minted_token_authenticates_as_target_user(app_state):
    app, state = app_state
    impersonation = (
        _client(app)
        .post(
            "/api/v1/root/impersonate",
            json={"target_user_id": str(state["user"].id)},
            headers=_bearer(_superadmin_token(state)),
        )
        .json()["access_token"]
    )

    resp = _client(app).get("/api/v1/auth/me", headers=_bearer(impersonation))
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["is_impersonating"] is True


def test_minted_token_rejected_by_require_superadmin(app_state):
    """Impersonating a superadmin must NOT grant superadmin privileges
    when require_superadmin is the gate."""
    app, state = app_state
    # Bootstrap a second superadmin so we have a real superadmin to target.
    runtime = app.state.asterion

    async def _add_second_superadmin():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                s = User(
                    email="root2@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
                    is_superadmin=True,
                )
                session.add(s)
            await session.refresh(s)
            return s

    second_super = asyncio.run(_add_second_superadmin())

    impersonation = (
        _client(app)
        .post(
            "/api/v1/root/impersonate",
            json={"target_user_id": str(second_super.id)},
            headers=_bearer(_superadmin_token(state)),
        )
        .json()["access_token"]
    )

    # Even though the impersonated user is_superadmin=True, the token type
    # is 'impersonation' so require_superadmin must reject it.
    resp = _client(app).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(state["user"].id)},
        headers=_bearer(impersonation),
    )
    assert resp.status_code == 403


# --- side effects ---


def test_impersonation_writes_log_row(app_state):
    app, state = app_state
    _client(app).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(state["user"].id)},
        headers=_bearer(_superadmin_token(state)),
    )
    rows = _impersonation_logs(app)
    assert len(rows) == 1
    assert rows[0].target_user_id == state["user"].id
    assert rows[0].superadmin_id == state["superadmin"].id
    assert rows[0].jti  # JTI present and indexable for later revocation


def test_impersonation_writes_audit_row(app_state):
    app, state = app_state
    _client(app).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(state["user"].id)},
        headers=_bearer(_superadmin_token(state)),
    )
    rows = _audit_rows(app, IMPERSONATION_START)
    assert len(rows) == 1
    assert rows[0].actor_user_id == state["superadmin"].id
    assert rows[0].resource == "users"
    assert rows[0].changes["target_user_id"] == str(state["user"].id)


def test_impersonation_records_jti_link(app_state):
    """The audit row's jti must match the ImpersonationLog row's jti so a
    later revocation can look up both."""
    app, state = app_state
    resp = _client(app).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(state["user"].id)},
        headers=_bearer(_superadmin_token(state)),
    )
    payload = decode_access_token(resp.json()["access_token"], secret_key=SECRET, algorithm=ALG)
    token_jti = payload["jti"]

    log_rows = _impersonation_logs(app)
    audit_rows = _audit_rows(app, IMPERSONATION_START)
    assert log_rows[0].jti == token_jti
    assert audit_rows[0].changes["jti"] == token_jti


# --- validation ---


def test_self_impersonation_rejected(app_state):
    app, state = app_state
    resp = _client(app).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(state["superadmin"].id)},
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 400


def test_unknown_target_user_returns_404(app_state):
    app, state = app_state
    resp = _client(app).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(uuid.uuid4())},
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 404


def test_inactive_target_user_returns_409(app_state):
    app, state = app_state
    runtime = app.state.asterion

    async def _deactivate():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                fresh = await session.get(User, state["user"].id)
                fresh.is_active = False

    asyncio.run(_deactivate())

    resp = _client(app).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(state["user"].id)},
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 409


def test_duration_clamped_via_validation(app_state):
    """duration_minutes outside [1, MAX] is rejected by pydantic."""
    app, state = app_state
    resp = _client(app).post(
        "/api/v1/root/impersonate",
        json={
            "target_user_id": str(state["user"].id),
            "duration_minutes": 1_000_000,
        },
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 422


def test_unknown_tenant_returns_404(app_state):
    app, state = app_state
    resp = _client(app).post(
        "/api/v1/root/impersonate",
        json={
            "target_user_id": str(state["user"].id),
            "tenant_id": str(uuid.uuid4()),
        },
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 404
