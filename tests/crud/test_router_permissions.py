"""HTTP integration tests for the CRUD router with permission-key authz.

Exercises POST/GET/PATCH/DELETE /api/v1/admin/{resource} against a real
FastAPI app with an in-memory SQLite database. Authorization is driven via
``require_tenant_auth_context`` dependency overrides so each test controls
the exact permission_keys granted to the simulated user.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from asterion import CoreAdminConfig, ModelAdmin, create_admin
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
    hashed_password = Column(String(255), nullable=True)
    internal_token = Column(String(255), nullable=True)
    is_system = Column(Boolean, nullable=False, default=False)


class WidgetAdmin(ModelAdmin):
    model = Widget
    list_display = ["id", "name", "color"]
    search_fields = ["name"]
    ordering = ["name"]
    readonly_fields = ["id"]
    protected_fields = ["internal_token"]


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'crud.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-crud-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: reg.register(WidgetAdmin),
    )

    runtime = application.state.asterion

    async def _setup_schema():
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

    asyncio.run(_setup_schema())

    yield application

    asyncio.run(runtime.db.dispose())


def _grant(app, keys: set[str]) -> None:
    """Inject an AdminContext with the given permission keys + a fake tenant.

    Mirrors the legacy behaviour where ``_require_resource_permission``
    only fires when there is a tenant context — pair with a tenant so
    permission checks actually run.
    """
    override_admin_context(
        app,
        principal=make_admin_principal(email="user@example.com"),
        tenant=make_admin_tenant("acme"),
        permissions=frozenset(keys),
    )


def _client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _seed_widgets(app, *, count: int = 3, is_system: bool = False) -> list[int]:
    runtime = app.state.asterion

    async def _seed():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        ids: list[int] = []
        async with factory() as session:
            async with session.begin():
                for index in range(count):
                    widget = Widget(
                        name=f"widget-{index}",
                        color=f"c{index}",
                        hashed_password="$2b$leak",
                        internal_token="topsecret",
                        is_system=is_system,
                    )
                    session.add(widget)
                    await session.flush()
                    ids.append(widget.id)
        return ids

    return asyncio.run(_seed())


# --- list ---


def test_list_requires_list_permission(app):
    _grant(app, set())
    resp = _client(app).get("/api/v1/admin/widgets")
    assert resp.status_code == 403


def test_list_with_list_permission(app):
    _seed_widgets(app, count=3)
    _grant(app, {"admin.widgets.list"})
    resp = _client(app).get("/api/v1/admin/widgets")
    assert resp.status_code == 200


def test_list_envelope_shape(app):
    _seed_widgets(app, count=3)
    _grant(app, {"admin.widgets.list"})
    body = _client(app).get("/api/v1/admin/widgets").json()
    assert set(body.keys()) == {"items", "total", "limit", "offset"}
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_list_pagination_bounds(app):
    _seed_widgets(app, count=3)
    _grant(app, {"admin.widgets.list"})
    body = _client(app).get("/api/v1/admin/widgets?limit=10000&offset=-5").json()
    # limit capped, offset clamped to 0
    assert body["limit"] <= 500
    assert body["offset"] == 0


def test_list_omits_hidden_fields(app):
    _seed_widgets(app, count=1)
    _grant(app, {"admin.widgets.list"})
    body = _client(app).get("/api/v1/admin/widgets").json()
    item = body["items"][0]
    assert "hashed_password" not in item
    assert "internal_token" not in item


def test_list_search_filters(app):
    _seed_widgets(app, count=3)
    _grant(app, {"admin.widgets.list"})
    body = _client(app).get("/api/v1/admin/widgets?search=widget-1").json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "widget-1"


# --- read ---


def test_read_requires_read_permission(app):
    ids = _seed_widgets(app, count=1)
    _grant(app, set())
    resp = _client(app).get(f"/api/v1/admin/widgets/{ids[0]}")
    assert resp.status_code == 403


def test_read_with_read_permission(app):
    ids = _seed_widgets(app, count=1)
    _grant(app, {"admin.widgets.read"})
    resp = _client(app).get(f"/api/v1/admin/widgets/{ids[0]}")
    assert resp.status_code == 200
    body = resp.json()
    assert "hashed_password" not in body
    assert "internal_token" not in body


def test_read_unknown_record_returns_404(app):
    _grant(app, {"admin.widgets.read"})
    resp = _client(app).get("/api/v1/admin/widgets/999999")
    assert resp.status_code == 404


def test_read_invalid_pk_returns_422(app):
    _grant(app, {"admin.widgets.read"})
    resp = _client(app).get("/api/v1/admin/widgets/not-an-int")
    assert resp.status_code == 422


# --- create ---


def test_create_requires_create_permission(app):
    _grant(app, {"admin.widgets.list"})
    resp = _client(app).post("/api/v1/admin/widgets", json={"name": "new"})
    assert resp.status_code == 403


def test_create_with_create_permission(app):
    _grant(app, {"admin.widgets.create"})
    resp = _client(app).post("/api/v1/admin/widgets", json={"name": "new", "color": "red"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "new"
    assert "hashed_password" not in body


def test_create_rejects_hidden_field(app):
    _grant(app, {"admin.widgets.create"})
    resp = _client(app).post(
        "/api/v1/admin/widgets",
        json={"name": "x", "hashed_password": "leak"},
    )
    assert resp.status_code == 422


def test_create_rejects_unknown_field(app):
    _grant(app, {"admin.widgets.create"})
    resp = _client(app).post("/api/v1/admin/widgets", json={"name": "x", "extra": 1})
    assert resp.status_code == 422


def test_create_rejects_readonly_field(app):
    _grant(app, {"admin.widgets.create"})
    resp = _client(app).post("/api/v1/admin/widgets", json={"id": 5, "name": "x"})
    assert resp.status_code == 422


# --- update ---


def test_update_requires_update_permission(app):
    ids = _seed_widgets(app, count=1)
    _grant(app, {"admin.widgets.read"})
    resp = _client(app).patch(f"/api/v1/admin/widgets/{ids[0]}", json={"name": "renamed"})
    assert resp.status_code == 403


def test_update_with_update_permission(app):
    ids = _seed_widgets(app, count=1)
    _grant(app, {"admin.widgets.update"})
    resp = _client(app).patch(f"/api/v1/admin/widgets/{ids[0]}", json={"name": "renamed"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed"


def test_update_rejects_hidden_field(app):
    ids = _seed_widgets(app, count=1)
    _grant(app, {"admin.widgets.update"})
    resp = _client(app).patch(
        f"/api/v1/admin/widgets/{ids[0]}",
        json={"internal_token": "leak"},
    )
    assert resp.status_code == 422


# --- delete ---


def test_delete_requires_delete_permission(app):
    ids = _seed_widgets(app, count=1)
    _grant(app, {"admin.widgets.read"})
    resp = _client(app).delete(f"/api/v1/admin/widgets/{ids[0]}")
    assert resp.status_code == 403


def test_delete_with_delete_permission(app):
    ids = _seed_widgets(app, count=1)
    _grant(app, {"admin.widgets.delete"})
    resp = _client(app).delete(f"/api/v1/admin/widgets/{ids[0]}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


def test_delete_is_system_record_returns_409(app):
    ids = _seed_widgets(app, count=1, is_system=True)
    _grant(app, {"admin.widgets.delete"})
    resp = _client(app).delete(f"/api/v1/admin/widgets/{ids[0]}")
    assert resp.status_code == 409


def test_delete_unknown_record_returns_404(app):
    _grant(app, {"admin.widgets.delete"})
    resp = _client(app).delete("/api/v1/admin/widgets/9999999")
    assert resp.status_code == 404


# --- wildcards ---


def test_resource_wildcard_grants_all_operations(app):
    ids = _seed_widgets(app, count=1)
    _grant(app, {"admin.widgets.*"})
    c = _client(app)
    assert c.get("/api/v1/admin/widgets").status_code == 200
    assert c.get(f"/api/v1/admin/widgets/{ids[0]}").status_code == 200
    assert c.patch(f"/api/v1/admin/widgets/{ids[0]}", json={"name": "z"}).status_code == 200
    assert c.post("/api/v1/admin/widgets", json={"name": "n"}).status_code == 201
    assert c.delete(f"/api/v1/admin/widgets/{ids[0]}").status_code == 200


def test_global_wildcard_grants_all_operations(app):
    ids = _seed_widgets(app, count=1)
    _grant(app, {"admin.*"})
    c = _client(app)
    assert c.get("/api/v1/admin/widgets").status_code == 200
    assert c.delete(f"/api/v1/admin/widgets/{ids[0]}").status_code == 200


def test_resource_wildcard_does_not_cross_namespace(app):
    _seed_widgets(app, count=1)
    _grant(app, {"other.widgets.*"})
    resp = _client(app).get("/api/v1/admin/widgets")
    assert resp.status_code == 403


# --- unknown resource ---


def test_unknown_resource_returns_404(app):
    _grant(app, {"admin.widgets.*"})
    resp = _client(app).get("/api/v1/admin/ghosts")
    assert resp.status_code == 404


def test_invalid_resource_name_returns_404(app):
    _grant(app, {"admin.widgets.*"})
    resp = _client(app).get("/api/v1/admin/Invalid%20Name!")
    assert resp.status_code == 404
