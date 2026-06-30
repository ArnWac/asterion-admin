"""HTTP tests for DELETE /api/v1/root/users/{id} (G2 anonymisation)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.audit.service import USER_ANONYMIZE
from asterion.auth.password import hash_password, verify_password
from asterion.auth.tokens import create_access_token, create_impersonation_token
from asterion.models.audit_log import AuditLog
from asterion.models.base import GlobalModel
from asterion.models.user import User
from asterion.privacy.anonymizer import anonymized_email

SECRET = "test-anon-secret"
ALG = "HS256"


@pytest.fixture
def app_state(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'anon.db'}"
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
                superadmin = User(
                    email="root@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
                    is_superadmin=True,
                )
                alice = User(
                    email="alice@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    full_name="Alice",
                    is_active=True,
                )
                session.add(superadmin)
                session.add(alice)
                await session.flush()
                # An audit row authored by alice — its actor PII must be redacted.
                session.add(
                    AuditLog(
                        method="POST",
                        path="/login",
                        status_code=200,
                        action="login_success",
                        actor_user_id=alice.id,
                        actor_label="alice@example.com",
                        ip_address="203.0.113.5",
                    )
                )
            await session.refresh(superadmin)
            await session.refresh(alice)
            state["superadmin"] = superadmin
            state["alice"] = alice

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
    u = state["alice"]
    return create_access_token(
        u.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=10,
        token_version=u.token_version,
    )


def _impersonation_token(state) -> str:
    return create_impersonation_token(
        state["alice"].id,
        impersonated_by_user_id=state["superadmin"].id,
        tenant_id=None,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=10,
        token_version=state["alice"].token_version,
    )


def test_anonymize_requires_superadmin(app_state):
    app, state = app_state
    resp = _client(app).delete(
        f"/api/v1/root/users/{state['alice'].id}",
        headers=_bearer(_user_token(state)),
    )
    assert resp.status_code == 403


def test_anonymize_rejects_impersonation_token(app_state):
    app, state = app_state
    resp = _client(app).delete(
        f"/api/v1/root/users/{state['alice'].id}",
        headers=_bearer(_impersonation_token(state)),
    )
    assert resp.status_code == 403


def test_anonymize_unknown_returns_404(app_state):
    app, state = app_state
    resp = _client(app).delete(
        f"/api/v1/root/users/{uuid.uuid4()}",
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 404


def test_anonymize_self_rejected(app_state):
    app, state = app_state
    resp = _client(app).delete(
        f"/api/v1/root/users/{state['superadmin'].id}",
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 409


def test_anonymize_superadmin_succeeds_and_tombstones(app_state):
    app, state = app_state
    alice_id = state["alice"].id
    resp = _client(app).delete(
        f"/api/v1/root/users/{alice_id}",
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["anonymized"] is True
    assert body["audit_rows_redacted"] == 1

    runtime = app.state.asterion

    async def _inspect():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            user = (await session.execute(select(User).where(User.id == alice_id))).scalar_one()
            audit_rows = (await session.execute(select(AuditLog))).scalars().all()
            return user, audit_rows

    user, audit_rows = asyncio.run(_inspect())

    # User PII tombstoned, row preserved (FK integrity).
    assert user.email == anonymized_email(alice_id)
    assert user.full_name is None
    assert user.is_active is False
    assert verify_password("hunter2-strong", user.hashed_password) is False

    # Alice's old audit row: actor PII redacted, row kept.
    login_rows = [r for r in audit_rows if r.action == "login_success"]
    assert len(login_rows) == 1
    assert login_rows[0].actor_label is None
    assert login_rows[0].ip_address is None
    assert login_rows[0].actor_user_id == alice_id

    # An anonymisation audit row was written by the superadmin.
    anon_rows = [r for r in audit_rows if r.action == USER_ANONYMIZE]
    assert len(anon_rows) == 1
    assert anon_rows[0].actor_user_id == state["superadmin"].id
    # No raw PII leaked into the anonymisation audit changes.
    assert "alice@example.com" not in (resp.text)
