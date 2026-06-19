"""HTTP integration tests: every audited route appends an AuditLog row.

Also asserts that an audit-write failure does not break the response path.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, String, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.actions import AdminAction
from asterion.audit import (
    ADMIN_ACTION,
    CRUD_CREATE,
    CRUD_DELETE,
    CRUD_UPDATE,
    LOGIN_FAILURE,
    LOGIN_SUCCESS,
)
from asterion.auth.password import hash_password
from asterion.models.audit_log import AuditLog
from asterion.models.base import GlobalModel
from asterion.models.user import User
from tests._helpers import make_admin_principal, make_admin_tenant, override_admin_context


class _AppBase(DeclarativeBase):
    pass


class Widget(_AppBase):
    __tablename__ = "widgets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)


class PingAction(AdminAction):
    name = "ping"
    label = "Ping"

    async def execute(self, records, session, user):
        return {"affected": len(records), "summary": "pong"}


class WidgetAdmin(ModelAdmin):
    model = Widget
    list_display = ["id", "name"]
    readonly_fields = ["id"]
    actions = [PingAction()]


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'audit-int.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-audit-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: reg.register(WidgetAdmin),
    )

    runtime = application.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
            await conn.run_sync(Widget.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add(
                    User(
                        email="alice@example.com",
                        hashed_password=hash_password("hunter2-strong"),
                        is_active=True,
                    )
                )

    asyncio.run(_setup())

    override_admin_context(
        application,
        principal=make_admin_principal(email="alice@example.com"),
        tenant=make_admin_tenant("acme"),
        permissions=frozenset({"admin.*"}),
    )

    yield application

    asyncio.run(runtime.db.dispose())


def _client(app):
    return TestClient(app, raise_server_exceptions=False)


def _audits(app, action: str | None = None) -> list[AuditLog]:
    runtime = app.state.asterion

    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            stmt = select(AuditLog)
            if action is not None:
                stmt = stmt.where(AuditLog.action == action)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    return asyncio.run(_go())


# --- login ---


def test_login_success_writes_audit(app):
    resp = _client(app).post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "hunter2-strong"},
    )
    assert resp.status_code == 200
    rows = _audits(app, LOGIN_SUCCESS)
    assert len(rows) == 1
    assert rows[0].path == "/api/v1/auth/login"
    assert rows[0].status_code == 200
    assert rows[0].changes == {"email": "alice@example.com"}
    assert rows[0].actor_label == "alice@example.com"


def test_login_failure_invalid_credentials_writes_audit(app):
    resp = _client(app).post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "wrong"},
    )
    assert resp.status_code == 401
    rows = _audits(app, LOGIN_FAILURE)
    assert len(rows) == 1
    assert rows[0].changes["reason"] == "invalid_credentials"
    # Password must NEVER appear in the audit row.
    flat = str(rows[0].changes)
    assert "wrong" not in flat


def test_login_failure_unknown_user_writes_audit(app):
    resp = _client(app).post(
        "/api/v1/auth/login",
        json={"email": "ghost@example.com", "password": "irrelevant"},
    )
    assert resp.status_code == 401
    rows = _audits(app, LOGIN_FAILURE)
    assert len(rows) == 1
    assert rows[0].actor_user_id is None


# --- crud ---


def _seed_widget(app, name: str = "w1") -> int:
    runtime = app.state.asterion

    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                w = Widget(name=name)
                session.add(w)
                await session.flush()
                return w.id

    return asyncio.run(_go())


def test_crud_create_writes_audit(app):
    resp = _client(app).post("/api/v1/admin/widgets", json={"name": "alpha"})
    assert resp.status_code == 201
    rows = _audits(app, CRUD_CREATE)
    assert len(rows) == 1
    assert rows[0].resource == "widgets"
    assert rows[0].record_id is not None
    assert rows[0].changes == {"name": "alpha"}


def test_crud_update_writes_audit(app):
    wid = _seed_widget(app)
    resp = _client(app).patch(f"/api/v1/admin/widgets/{wid}", json={"name": "renamed"})
    assert resp.status_code == 200
    rows = _audits(app, CRUD_UPDATE)
    assert len(rows) == 1
    assert rows[0].record_id == str(wid)


def test_crud_delete_writes_audit(app):
    wid = _seed_widget(app)
    resp = _client(app).delete(f"/api/v1/admin/widgets/{wid}")
    assert resp.status_code == 200
    rows = _audits(app, CRUD_DELETE)
    assert len(rows) == 1
    assert rows[0].record_id == str(wid)


def test_crud_failure_does_not_write_audit(app):
    """A 422 from clean_write_payload (unknown field) must NOT emit an audit row."""
    resp = _client(app).post("/api/v1/admin/widgets", json={"name": "x", "unknown": 1})
    assert resp.status_code == 422
    assert _audits(app, CRUD_CREATE) == []


# --- admin actions ---


def test_admin_action_writes_audit(app):
    wid = _seed_widget(app)
    resp = _client(app).post("/api/v1/admin/widgets/_actions/ping", json={"ids": [wid]})
    assert resp.status_code == 200
    rows = _audits(app, ADMIN_ACTION)
    assert len(rows) == 1
    assert rows[0].resource == "widgets"
    assert rows[0].changes["action"] == "ping"
    assert rows[0].changes["ids"] == [str(wid)]


# --- failure tolerance ---


def test_audit_write_failure_does_not_break_request(app, monkeypatch):
    """Plan §Phase 7: 'Audit failure must not break the functional response
    path.' Forcing the in-session audit helper to raise must still produce
    a 2xx and a created widget."""
    from asterion.audit import service as audit_service

    async def _boom(*args, **kwargs):
        raise RuntimeError("audit went up in flames")

    monkeypatch.setattr(audit_service, "record_audit_in_session", _boom)
    # crud/router and actions/router both import the symbol at module load,
    # so we patch their bound copies as well.
    from asterion.actions import router as actions_router
    from asterion.crud import router as crud_router

    monkeypatch.setattr(crud_router, "record_audit_in_session", _boom)
    monkeypatch.setattr(actions_router, "record_audit_in_session", _boom)

    resp = _client(app).post("/api/v1/admin/widgets", json={"name": "still works"})
    assert resp.status_code == 201
