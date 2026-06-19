"""HTTP integration tests for the admin actions router (Phase 6).

Endpoint: ``POST /api/v1/admin/{resource}/_actions/{action}``.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.actions import AdminAction, BulkDeleteAction
from asterion.auth.password import hash_password
from asterion.models.base import GlobalModel
from asterion.models.user import User
from tests._helpers import make_admin_principal, make_admin_tenant, override_admin_context


class _AppBase(DeclarativeBase):
    pass


class Widget(_AppBase):
    __tablename__ = "widgets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    color = Column(String(50), nullable=True)


class RenameAction(AdminAction):
    name = "rename"
    label = "Rename selected"

    async def execute(self, records, session, user):
        for record in records:
            record.name = f"renamed-{record.id}"
        await session.flush()
        return {"affected": len(records), "summary": f"Renamed {len(records)}"}


class NoCommitAction(AdminAction):
    """Asserts the action body sees the request's transaction (not autocommit)."""

    name = "touch"
    label = "Touch"

    async def execute(self, records, session, user):
        # Mutate but don't flush — verify caller may still rollback.
        for record in records:
            record.name = "touched"
        return {"affected": len(records), "summary": "ok"}


class BadResultAction(AdminAction):
    name = "bad"
    label = "Bad"

    async def execute(self, records, session, user):
        return "not a dict"


class WidgetAdmin(ModelAdmin):
    model = Widget
    list_display = ["id", "name"]
    readonly_fields = ["id"]
    actions = [
        BulkDeleteAction(),
        RenameAction(),
        NoCommitAction(),
        BadResultAction(),
    ]


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'actions.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-actions-secret",
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
                        email="user@example.com",
                        hashed_password=hash_password("hunter2-strong"),
                        is_active=True,
                    )
                )

    asyncio.run(_setup())

    # Default: an authenticated user, no tenant, no permissions. Auth-only
    # endpoints (contract) pass; permission-gated endpoints (CRUD/actions)
    # 403 unless a test overrides with _grant().
    override_admin_context(
        app=application, principal=make_admin_principal(email="user@example.com")
    )

    yield application

    asyncio.run(runtime.db.dispose())


def _grant(app, keys: set[str]) -> None:
    override_admin_context(
        app,
        principal=make_admin_principal(email="user@example.com"),
        tenant=make_admin_tenant("acme"),
        permissions=frozenset(keys),
    )


def _client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _seed_widgets(app, count: int = 3) -> list[int]:
    runtime = app.state.asterion

    async def _seed():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        ids: list[int] = []
        async with factory() as session:
            async with session.begin():
                for index in range(count):
                    widget = Widget(name=f"widget-{index}", color=f"c{index}")
                    session.add(widget)
                    await session.flush()
                    ids.append(widget.id)
        return ids

    return asyncio.run(_seed())


def _list_widgets(app) -> list[tuple[int, str]]:
    runtime = app.state.asterion

    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            from sqlalchemy import select

            result = await session.execute(select(Widget.id, Widget.name))
            return list(result.all())

    return asyncio.run(_go())


# --- success paths ---


def test_rename_action_runs_with_correct_permission(app):
    ids = _seed_widgets(app, count=2)
    _grant(app, {"admin.widgets.rename"})
    resp = _client(app).post("/api/v1/admin/widgets/_actions/rename", json={"ids": ids})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["affected"] == 2
    rows = _list_widgets(app)
    assert all(row[1].startswith("renamed-") for row in rows)


def test_bulk_delete_action_actually_deletes(app):
    ids = _seed_widgets(app, count=3)
    _grant(app, {"admin.widgets.delete"})
    resp = _client(app).post("/api/v1/admin/widgets/_actions/delete", json={"ids": ids})
    assert resp.status_code == 200
    assert resp.json()["affected"] == 3
    assert _list_widgets(app) == []


def test_resource_wildcard_grants_action(app):
    ids = _seed_widgets(app, count=1)
    _grant(app, {"admin.widgets.*"})
    resp = _client(app).post("/api/v1/admin/widgets/_actions/rename", json={"ids": ids})
    assert resp.status_code == 200


def test_global_wildcard_grants_action(app):
    ids = _seed_widgets(app, count=1)
    _grant(app, {"admin.*"})
    resp = _client(app).post("/api/v1/admin/widgets/_actions/delete", json={"ids": ids})
    assert resp.status_code == 200


def test_empty_ids_is_no_op(app):
    _grant(app, {"admin.widgets.rename"})
    resp = _client(app).post("/api/v1/admin/widgets/_actions/rename", json={"ids": []})
    assert resp.status_code == 200
    assert resp.json()["affected"] == 0


# --- authz ---


def test_missing_permission_returns_403(app):
    ids = _seed_widgets(app, count=1)
    _grant(app, {"admin.widgets.list"})
    resp = _client(app).post("/api/v1/admin/widgets/_actions/rename", json={"ids": ids})
    assert resp.status_code == 403


def test_action_specific_permission_does_not_grant_others(app):
    ids = _seed_widgets(app, count=1)
    _grant(app, {"admin.widgets.rename"})
    resp = _client(app).post("/api/v1/admin/widgets/_actions/delete", json={"ids": ids})
    assert resp.status_code == 403


def test_cross_namespace_wildcard_denied(app):
    ids = _seed_widgets(app, count=1)
    _grant(app, {"other.widgets.*"})
    resp = _client(app).post("/api/v1/admin/widgets/_actions/rename", json={"ids": ids})
    assert resp.status_code == 403


# --- 404s ---


def test_unknown_resource_returns_404(app):
    _grant(app, {"admin.*"})
    resp = _client(app).post("/api/v1/admin/ghosts/_actions/delete", json={"ids": []})
    assert resp.status_code == 404


def test_unknown_action_returns_404(app):
    _grant(app, {"admin.*"})
    resp = _client(app).post("/api/v1/admin/widgets/_actions/no_such_action", json={"ids": []})
    assert resp.status_code == 404


def test_invalid_action_name_returns_404(app):
    _grant(app, {"admin.*"})
    resp = _client(app).post("/api/v1/admin/widgets/_actions/Bad-Name", json={"ids": []})
    assert resp.status_code == 404


def test_invalid_resource_name_returns_404(app):
    _grant(app, {"admin.*"})
    resp = _client(app).post("/api/v1/admin/Invalid%20Name!/_actions/delete", json={"ids": []})
    assert resp.status_code == 404


# --- payload validation ---


def test_missing_body_returns_422(app):
    _grant(app, {"admin.widgets.rename"})
    resp = _client(app).post("/api/v1/admin/widgets/_actions/rename")
    assert resp.status_code == 422


def test_ids_field_must_be_list(app):
    _grant(app, {"admin.widgets.rename"})
    resp = _client(app).post(
        "/api/v1/admin/widgets/_actions/rename",
        json={"ids": "not-a-list"},
    )
    assert resp.status_code == 422


def test_default_ids_empty_list_when_omitted(app):
    _grant(app, {"admin.widgets.rename"})
    resp = _client(app).post("/api/v1/admin/widgets/_actions/rename", json={})
    assert resp.status_code == 200
    assert resp.json()["affected"] == 0


# --- result handling ---


def test_non_dict_result_returns_500(app):
    _grant(app, {"admin.widgets.bad"})
    resp = _client(app).post("/api/v1/admin/widgets/_actions/bad", json={"ids": []})
    assert resp.status_code == 500


# --- contract surface ---


def test_actions_visible_in_contract(app):
    resp = _client(app).get("/api/v1/admin/_contract/widgets")
    # contract router requires get_current_user override too — reuse the app fixture's
    assert resp.status_code == 200
    names = [a["name"] for a in resp.json()["admin_actions"]]
    assert "delete" in names
    assert "rename" in names


# --- unit: AdminAction base contract ---


def test_admin_action_base_execute_not_implemented():
    class Foo(AdminAction):
        name = "foo"
        label = "Foo"

    action = Foo()

    async def _run():
        await action.execute([], None, None)

    with pytest.raises(NotImplementedError):
        asyncio.run(_run())


def test_admin_action_to_dict_shape():
    """C3 adds ``confirm``, ``bulk``, ``input_schema`` (all with safe
    defaults) so the UI can render confirmation prompts and typed
    forms without a second round-trip."""

    class Foo(AdminAction):
        name = "foo"
        label = "Foo!"

    assert Foo().to_dict() == {
        "name": "foo",
        "label": "Foo!",
        "confirm": False,
        "bulk": True,
        "input_schema": None,
    }
