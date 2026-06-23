"""FK-picker options endpoint + label_field heuristic (v0.1.24).

``GET /api/v1/admin/{resource}/_options/{field}`` enumerates the target
table's rows as ``{value, label}`` pairs so the form can render a dropdown for
a foreign-key column instead of a raw id input. The label column is the target
admin's :attr:`ModelAdmin.label_field` (explicit ``display_field`` or a
heuristic). Authorization requires ``read`` on the owning resource and ``list``
on the target.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.models.base import GlobalModel
from tests._helpers import make_admin_principal, make_admin_tenant, override_admin_context


class _AppBase(DeclarativeBase):
    pass


class Category(_AppBase):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)


class Item(_AppBase):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    # A reference id with NO DB-level foreign key — resolved via the custom
    # resolve_fk_options hook + a widgets override (mirrors membership_id).
    owner_ref = Column(Integer, nullable=True)


class CategoryAdmin(ModelAdmin):
    model = Category
    list_display = ["id", "name"]


class ItemAdmin(ModelAdmin):
    model = Item
    list_display = ["id", "title", "category_id"]
    widgets = {"owner_ref": "foreign_key"}

    async def resolve_fk_options(self, field, *, session, ctx=None, q=None, limit=100):
        if field != "owner_ref":
            return None
        opts = [
            {"value": "1", "label": "owner-one"},
            {"value": "2", "label": "owner-two"},
        ]
        if q:
            opts = [o for o in opts if q in o["label"]]
        return opts


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'fk.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-fk-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: (reg.register(CategoryAdmin), reg.register(ItemAdmin)),
    )
    runtime = application.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
            await conn.run_sync(_AppBase.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add_all(
                    [Category(name="Beta"), Category(name="Alpha"), Category(name="Gamma")]
                )

    asyncio.run(_setup())
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


# --- label_field heuristic (unit) ---


def test_label_field_prefers_name():
    assert CategoryAdmin().label_field == "name"


def test_label_field_respects_explicit_display_field():
    class _A(ModelAdmin):
        model = Category
        display_field = "id"

    assert _A().label_field == "id"


def test_label_field_falls_back_to_pk_when_no_candidate():
    class Bare(_AppBase):
        __tablename__ = "bare_fk_test"
        id = Column(Integer, primary_key=True)
        amount = Column(Integer, nullable=True)

    class _A(ModelAdmin):
        model = Bare

    assert _A().label_field == "id"


# --- options endpoint ---


def test_options_returns_value_label_pairs_sorted_by_label(app):
    _grant(app, {"admin.items.read", "admin.categories.list"})
    resp = _client(app).get("/api/v1/admin/items/_options/category_id")
    assert resp.status_code == 200
    body = resp.json()
    assert body["registered"] is True
    labels = [o["label"] for o in body["options"]]
    assert labels == ["Alpha", "Beta", "Gamma"]  # ordered by label
    # value is the stringified primary key
    assert all(isinstance(o["value"], str) for o in body["options"])


def test_options_search_filters_by_label(app):
    _grant(app, {"admin.items.read", "admin.categories.list"})
    resp = _client(app).get("/api/v1/admin/items/_options/category_id?q=alph")
    assert resp.status_code == 200
    assert [o["label"] for o in resp.json()["options"]] == ["Alpha"]


def test_options_requires_read_on_owning_resource(app):
    _grant(app, {"admin.categories.list"})  # no items.read
    resp = _client(app).get("/api/v1/admin/items/_options/category_id")
    assert resp.status_code == 403


def test_options_requires_list_on_target_resource(app):
    _grant(app, {"admin.items.read"})  # no categories.list
    resp = _client(app).get("/api/v1/admin/items/_options/category_id")
    assert resp.status_code == 403


def test_options_non_fk_field_returns_404(app):
    _grant(app, {"admin.items.read", "admin.categories.list"})
    resp = _client(app).get("/api/v1/admin/items/_options/title")
    assert resp.status_code == 404


def test_options_unknown_field_returns_404(app):
    _grant(app, {"admin.items.read", "admin.categories.list"})
    resp = _client(app).get("/api/v1/admin/items/_options/nope")
    assert resp.status_code == 404


# --- custom resolver (cross-schema / no DB FK) ---


def test_custom_resolver_supplies_options_without_db_fk(app):
    # owner_ref has no ForeignKey, but resolve_fk_options handles it.
    _grant(app, {"admin.items.read"})
    resp = _client(app).get("/api/v1/admin/items/_options/owner_ref")
    assert resp.status_code == 200
    body = resp.json()
    assert body["registered"] is True
    assert body["options"] == [
        {"value": "1", "label": "owner-one"},
        {"value": "2", "label": "owner-two"},
    ]


def test_custom_resolver_honours_search(app):
    _grant(app, {"admin.items.read"})
    resp = _client(app).get("/api/v1/admin/items/_options/owner_ref?q=two")
    assert resp.status_code == 200
    assert resp.json()["options"] == [{"value": "2", "label": "owner-two"}]


def test_default_resolve_fk_options_returns_none():
    import asyncio

    assert asyncio.run(CategoryAdmin().resolve_fk_options("name", session=None)) is None
