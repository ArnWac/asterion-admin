"""D4: filter-aware export.

Validates that ``GET /{resource}/_export`` honours the same
``?filter_<field>=value`` query params as the list endpoint, so the
"export current view" feature truly mirrors the on-screen selection.
"""

from __future__ import annotations

import asyncio
import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.auth.password import hash_password
from asterion.extensions.import_export import ImportExportExtension
from asterion.models.base import GlobalModel
from asterion.models.user import User
from tests._helpers import make_admin_principal, make_admin_tenant, override_admin_context


class _Base(DeclarativeBase):
    pass


class _FilterableWidget(_Base):
    __tablename__ = "d4_widgets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    color = Column(String, nullable=True)
    count = Column(Integer, nullable=True)


class _FilterableWidgetAdmin(ModelAdmin):
    model = _FilterableWidget
    list_display = ["id", "name", "color", "count"]
    search_fields = ["name"]
    ordering = ["id"]
    filter_fields = ["color", "count"]


def _grant(app) -> None:
    override_admin_context(
        app,
        principal=make_admin_principal(email="alice@example.com"),
        tenant=make_admin_tenant("acme"),
        permissions=frozenset({"admin.*"}),
    )


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'export_filters.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-export-filters-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: reg.register(_FilterableWidgetAdmin),
        extensions=[ImportExportExtension()],
    )
    runtime = application.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
            await conn.run_sync(_FilterableWidget.metadata.create_all)
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
    override_admin_context(application, principal=make_admin_principal(email="alice@example.com"))

    yield application
    asyncio.run(runtime.db.dispose())


def _seed(app, rows: list[dict]) -> None:
    runtime = app.state.asterion

    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                for row in rows:
                    session.add(_FilterableWidget(**row))

    asyncio.run(_go())


def _client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _parse(body: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(body)))


# ---------------------------------------------------------------------------
# Filter application
# ---------------------------------------------------------------------------


def test_export_with_single_filter_narrows_rows(app):
    _grant(app)
    _seed(
        app,
        [
            {"name": "a", "color": "red", "count": 1},
            {"name": "b", "color": "blue", "count": 1},
            {"name": "c", "color": "red", "count": 2},
        ],
    )
    resp = _client(app).get("/api/v1/admin/d4_widgets/_export?format=csv&filter_color=red")
    assert resp.status_code == 200, resp.text
    rows = _parse(resp.text)
    assert sorted(r["name"] for r in rows) == ["a", "c"]


def test_export_with_combined_filters_applies_and(app):
    _grant(app)
    _seed(
        app,
        [
            {"name": "a", "color": "red", "count": 1},
            {"name": "b", "color": "red", "count": 2},
            {"name": "c", "color": "blue", "count": 1},
        ],
    )
    resp = _client(app).get(
        "/api/v1/admin/d4_widgets/_export?format=csv&filter_color=red&filter_count=2"
    )
    assert resp.status_code == 200
    rows = _parse(resp.text)
    assert [r["name"] for r in rows] == ["b"]


def test_export_filter_composes_with_search(app):
    _grant(app)
    _seed(
        app,
        [
            {"name": "alpha", "color": "red"},
            {"name": "alphabet", "color": "red"},
            {"name": "alpha", "color": "blue"},
        ],
    )
    resp = _client(app).get(
        "/api/v1/admin/d4_widgets/_export?format=csv&filter_color=red&search=alphabet"
    )
    assert resp.status_code == 200
    rows = _parse(resp.text)
    assert [r["name"] for r in rows] == ["alphabet"]


# ---------------------------------------------------------------------------
# Negative paths
# ---------------------------------------------------------------------------


def test_export_unknown_filter_field_returns_422(app):
    _grant(app)
    _seed(app, [{"name": "a", "color": "red"}])
    resp = _client(app).get("/api/v1/admin/d4_widgets/_export?format=csv&filter_ghost=anything")
    assert resp.status_code == 422


def test_export_bad_integer_filter_returns_422(app):
    _grant(app)
    _seed(app, [{"name": "a", "count": 1}])
    resp = _client(app).get("/api/v1/admin/d4_widgets/_export?format=csv&filter_count=not-an-int")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Selection-based export still ignores filters (existing contract)
# ---------------------------------------------------------------------------


def test_export_with_ids_ignores_filter_params(app):
    """When the caller passes explicit ``?ids=...`` the route returns
    exactly that selection — filters are not applied on top (matches
    the existing ``ids`` + ``search`` interaction)."""
    _grant(app)
    _seed(
        app,
        [
            {"name": "a", "color": "red"},
            {"name": "b", "color": "blue"},
        ],
    )
    # color=red would normally exclude "b", but the explicit ids list wins.
    resp = _client(app).get("/api/v1/admin/d4_widgets/_export?format=csv&ids=2&filter_color=red")
    assert resp.status_code == 200
    rows = _parse(resp.text)
    assert [r["name"] for r in rows] == ["b"]


# ---------------------------------------------------------------------------
# Audit row carries the filter dict
# ---------------------------------------------------------------------------


def test_export_audit_records_filters(app):
    from sqlalchemy import select

    from asterion.models.audit_log import AuditLog

    _grant(app)
    _seed(app, [{"name": "a", "color": "red"}])
    resp = _client(app).get("/api/v1/admin/d4_widgets/_export?format=csv&filter_color=red")
    assert resp.status_code == 200

    runtime = app.state.asterion

    async def _read():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            result = await session.execute(
                select(AuditLog).order_by(AuditLog.created_at.desc()).limit(1)
            )
            return result.scalar_one()

    audit = asyncio.run(_read())
    assert "filters" in audit.changes
    assert audit.changes["filters"] == {"filter_color": "red"}
