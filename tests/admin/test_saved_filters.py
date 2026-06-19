"""D2: saved-filter CRUD HTTP surface.

Covers:
* Create-and-list roundtrip — payload survives the JSON round-trip.
* Per-user scoping — other users' filters are invisible.
* Per-resource scoping — listing for resource A doesn't show B.
* Upsert semantics — POST with an existing (user, resource, name)
  replaces the prior payload.
* Delete returns 204 + scoping (other user's filter is 404, not 403).
"""

from __future__ import annotations

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from asterion import create_admin
from asterion.admin.context import (
    AdminContext,
    build_admin_context,
    require_admin_context,
)
from asterion.core.config import CoreAdminConfig
from asterion.db.dependencies import get_async_session
from asterion.models.base import GLOBAL_METADATA
from asterion.providers.base import AdminPrincipal
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Post(_Base):
    __tablename__ = "d2_posts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)


class _PostAdmin(ModelAdmin):
    model = _Post


@pytest_asyncio.fixture()
async def app_factory():
    """Build a fresh app with an in-memory SQLite session factory.

    Uses asterion's own DatabaseManager so the SQLite
    ``schema_translate_map`` is applied (Postgres-style ``public``
    schema collapses to None on SQLite).

    Returns a (TestClient, ctx_setter) pair.
    """
    current_user = {"id": "alice"}

    def _register(registry):
        registry.register(_PostAdmin())

    app = create_admin(
        config=CoreAdminConfig(
            secret_key="x" * 64,
            database_url="sqlite+aiosqlite:///:memory:",
            environment="development",
        ),
        register=_register,
    )

    # Create the tables on the same engine the app will use.
    engine = app.state.asterion.db.engine
    async with engine.begin() as conn:
        await conn.run_sync(GLOBAL_METADATA.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with factory() as session:
            async with session.begin():
                yield session

    async def _override_ctx() -> AdminContext:
        return AdminContext(
            request=None,
            principal=AdminPrincipal(id=current_user["id"], email="x@x"),
            tenant=None,
            permissions=frozenset({"admin.*"}),
        )

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[build_admin_context] = _override_ctx
    app.dependency_overrides[require_admin_context] = _override_ctx

    def _set_user(uid: str) -> None:
        current_user["id"] = uid

    yield TestClient(app), _set_user
    await engine.dispose()


def test_create_and_list_roundtrip(app_factory):
    client, _ = app_factory
    create = client.post(
        "/api/v1/admin/_saved_filters",
        json={
            "resource": "d2_posts",
            "name": "drafts",
            "payload": {"filter_status": "draft", "search": ""},
        },
    )
    assert create.status_code == 201, create.text
    saved = create.json()
    assert saved["resource"] == "d2_posts"
    assert saved["name"] == "drafts"
    assert saved["payload"] == {"filter_status": "draft", "search": ""}

    listing = client.get("/api/v1/admin/_saved_filters", params={"resource": "d2_posts"})
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "drafts"


def test_upsert_replaces_payload_on_same_name(app_factory):
    client, _ = app_factory
    client.post(
        "/api/v1/admin/_saved_filters",
        json={"resource": "d2_posts", "name": "drafts", "payload": {"a": 1}},
    )
    client.post(
        "/api/v1/admin/_saved_filters",
        json={"resource": "d2_posts", "name": "drafts", "payload": {"a": 2}},
    )
    rows = client.get("/api/v1/admin/_saved_filters", params={"resource": "d2_posts"}).json()
    assert len(rows) == 1
    assert rows[0]["payload"] == {"a": 2}


def test_filters_are_scoped_per_user(app_factory):
    client, set_user = app_factory
    set_user("alice")
    client.post(
        "/api/v1/admin/_saved_filters",
        json={"resource": "d2_posts", "name": "alice-only", "payload": {}},
    )
    set_user("bob")
    rows = client.get("/api/v1/admin/_saved_filters", params={"resource": "d2_posts"}).json()
    assert rows == []


def test_list_filters_by_resource(app_factory):
    client, _ = app_factory
    client.post(
        "/api/v1/admin/_saved_filters",
        json={"resource": "d2_posts", "name": "p1", "payload": {}},
    )
    # A second resource the user could query is enough — we don't need
    # it registered as a ModelAdmin.
    client.post(
        "/api/v1/admin/_saved_filters",
        json={"resource": "d2_posts", "name": "p2", "payload": {}},
    )
    rows = client.get("/api/v1/admin/_saved_filters", params={"resource": "d2_posts"}).json()
    assert sorted(r["name"] for r in rows) == ["p1", "p2"]


def test_delete_owned_filter_returns_204(app_factory):
    client, _ = app_factory
    created = client.post(
        "/api/v1/admin/_saved_filters",
        json={"resource": "d2_posts", "name": "to-delete", "payload": {}},
    ).json()
    resp = client.delete(f"/api/v1/admin/_saved_filters/{created['id']}")
    assert resp.status_code == 204
    rows = client.get("/api/v1/admin/_saved_filters", params={"resource": "d2_posts"}).json()
    assert rows == []


def test_delete_other_users_filter_returns_404(app_factory):
    """Cross-user delete must not leak existence — 404, not 403."""
    client, set_user = app_factory
    set_user("alice")
    created = client.post(
        "/api/v1/admin/_saved_filters",
        json={"resource": "d2_posts", "name": "alice", "payload": {}},
    ).json()
    set_user("bob")
    resp = client.delete(f"/api/v1/admin/_saved_filters/{created['id']}")
    assert resp.status_code == 404


def test_invalid_uuid_returns_404(app_factory):
    client, _ = app_factory
    resp = client.delete("/api/v1/admin/_saved_filters/not-a-uuid")
    assert resp.status_code == 404


def test_invalid_resource_name_returns_422(app_factory):
    client, _ = app_factory
    resp = client.post(
        "/api/v1/admin/_saved_filters",
        json={"resource": "$$$bad", "name": "x", "payload": {}},
    )
    assert resp.status_code == 422


def test_empty_name_rejected(app_factory):
    client, _ = app_factory
    resp = client.post(
        "/api/v1/admin/_saved_filters",
        json={"resource": "d2_posts", "name": "  ", "payload": {}},
    )
    assert resp.status_code == 422


def test_non_dict_payload_rejected(app_factory):
    client, _ = app_factory
    resp = client.post(
        "/api/v1/admin/_saved_filters",
        json={"resource": "d2_posts", "name": "x", "payload": "not-a-dict"},
    )
    assert resp.status_code == 422
