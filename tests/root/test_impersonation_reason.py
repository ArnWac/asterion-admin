"""G9 — impersonation requires a documented reason.

``CoreAdminConfig.impersonation_require_reason`` (default True) makes the
``POST {root}/impersonate`` route reject a request without a reason (422) and
persists the reason on the ``ImpersonationLog`` row + the audit ``changes`` so
support access to another user's data always carries a documented purpose.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.audit import IMPERSONATION_START
from asterion.auth.password import hash_password
from asterion.auth.tokens import create_access_token
from asterion.models.audit_log import AuditLog
from asterion.models.base import GlobalModel
from asterion.models.impersonation_log import ImpersonationLog
from asterion.models.user import User

SECRET = "test-impersonate-reason-secret"
ALG = "HS256"


def _make_app(tmp_path, *, require_reason: bool):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'imp_reason.db'}"
    app = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
            impersonation_require_reason=require_reason,
        )
    )
    runtime = app.state.asterion
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
                normal = User(
                    email="alice@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
                    is_superadmin=False,
                )
                session.add_all([superadmin, normal])
            await session.refresh(superadmin)
            await session.refresh(normal)
            state["superadmin"] = superadmin
            state["user"] = normal

    asyncio.run(_setup())
    return app, state


def _superadmin_token(state) -> str:
    su = state["superadmin"]
    return create_access_token(
        su.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=10,
        token_version=su.token_version,
    )


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _rows(app, model, **where):
    runtime = app.state.asterion

    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            stmt = select(model)
            return list((await session.execute(stmt)).scalars().all())

    return asyncio.run(_go())


def test_missing_reason_is_rejected_by_default(tmp_path):
    app, state = _make_app(tmp_path, require_reason=True)
    resp = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(state["user"].id)},
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "validation_error"
    # No log row written on rejection.
    assert _rows(app, ImpersonationLog) == []


def test_blank_reason_is_rejected(tmp_path):
    app, state = _make_app(tmp_path, require_reason=True)
    resp = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(state["user"].id), "reason": "   "},
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 422, resp.text


def test_reason_persisted_on_log_and_audit(tmp_path):
    app, state = _make_app(tmp_path, require_reason=True)
    resp = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/root/impersonate",
        json={
            "target_user_id": str(state["user"].id),
            "reason": "Support ticket #4711 — investigate missing punch",
        },
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 200, resp.text

    logs = _rows(app, ImpersonationLog)
    assert len(logs) == 1
    assert logs[0].reason == "Support ticket #4711 — investigate missing punch"

    audits = [a for a in _rows(app, AuditLog) if a.action == IMPERSONATION_START]
    assert len(audits) == 1
    assert audits[0].changes["reason"] == "Support ticket #4711 — investigate missing punch"


def test_reason_optional_when_disabled(tmp_path):
    app, state = _make_app(tmp_path, require_reason=False)
    resp = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/root/impersonate",
        json={"target_user_id": str(state["user"].id)},
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 200, resp.text
    logs = _rows(app, ImpersonationLog)
    assert len(logs) == 1
    assert logs[0].reason is None
