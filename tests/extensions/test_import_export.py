"""Tests for the CSV export extension.

Boots a minimal admin app with ``import_export`` mounted and a single
registered ``WidgetAdmin``. Verifies:

* GET /{resource}/_export?format=csv returns a 200 with CSV body
* unsupported formats are 400
* unknown resources are 404
* permission gate (admin.<resource>.list) is enforced
* row cap is applied
* search filter narrows the export
* audit row is written
"""

from __future__ import annotations

import asyncio
import csv
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, String, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from adminfoundry import CoreAdminConfig, ModelAdmin, create_admin
from adminfoundry.auth.dependencies import get_current_user
from adminfoundry.auth.password import hash_password
from adminfoundry.extensions.import_export import (
    EXPORT_AUDIT_ACTION,
    IMPORT_AUDIT_ACTION,
    MAX_EXPORT_ROWS,
    MAX_IMPORT_ROWS,
)
from adminfoundry.extensions.import_export import (
    register as csv_export,
)
from adminfoundry.models.audit_log import AuditLog
from adminfoundry.models.base import GlobalModel
from adminfoundry.models.user import User
from adminfoundry.tenancy.context import TenantAuthContext, TenantContext
from adminfoundry.tenancy.dependencies import require_tenant_auth_context


class _Base(DeclarativeBase):
    pass


class Widget(_Base):
    __tablename__ = "export_widgets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    color = Column(String, nullable=True)
    api_secret = Column(String, nullable=True)


class WidgetAdmin(ModelAdmin):
    model = Widget
    list_display = ["id", "name", "color"]
    search_fields = ["name"]
    ordering = ["id"]
    protected_fields = ["api_secret"]


def _tenant_ctx() -> TenantContext:
    return TenantContext(
        id=uuid.uuid4(), slug="acme", name="Acme",
        is_active=True, schema_name="tenant_acme",
    )


class _StubMembership:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.tenant_id = uuid.uuid4()
        self.is_active = True


def _grant(app, keys: set[str]) -> None:
    async def _override():
        return TenantAuthContext(
            tenant=_tenant_ctx(),
            membership=_StubMembership(),
            roles=[],
            permission_keys=set(keys),
        )

    app.dependency_overrides[require_tenant_auth_context] = _override


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'export.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-export-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: reg.register(WidgetAdmin),
        extensions=[csv_export],
    )
    runtime = application.state.adminfoundry

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

    async def _current_user_override():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            row = await session.execute(select(User).where(User.email == "alice@example.com"))
            return row.scalar_one()

    application.dependency_overrides[get_current_user] = _current_user_override

    yield application
    asyncio.run(runtime.db.dispose())


def _seed_widgets(app, count: int) -> None:
    runtime = app.state.adminfoundry

    async def _go():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                for i in range(count):
                    session.add(
                        Widget(
                            name=f"widget-{i:03d}",
                            color="red" if i % 2 == 0 else "blue",
                            api_secret="topsecret",
                        )
                    )

    asyncio.run(_go())


def _client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _parse_csv(body: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(body)))


# --- happy path ---


def test_export_returns_csv_with_list_display_columns(app):
    _seed_widgets(app, 3)
    _grant(app, {"admin.export_widgets.list"})
    resp = _client(app).get("/api/v1/admin/export_widgets/_export?format=csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert 'filename="export_widgets.csv"' in resp.headers["content-disposition"]

    rows = _parse_csv(resp.text)
    assert len(rows) == 3
    assert list(rows[0].keys()) == ["id", "name", "color"]
    assert [r["name"] for r in rows] == ["widget-000", "widget-001", "widget-002"]


def test_export_csv_omits_protected_fields(app):
    _seed_widgets(app, 1)
    _grant(app, {"admin.export_widgets.list"})
    resp = _client(app).get("/api/v1/admin/export_widgets/_export?format=csv")
    assert resp.status_code == 200
    assert "api_secret" not in resp.text
    assert "topsecret" not in resp.text


# --- input handling ---


def test_export_rejects_unsupported_format(app):
    _grant(app, {"admin.export_widgets.list"})
    resp = _client(app).get("/api/v1/admin/export_widgets/_export?format=json")
    assert resp.status_code == 400


def test_export_unknown_resource_returns_404(app):
    _grant(app, {"admin.unknown.list"})
    resp = _client(app).get("/api/v1/admin/unknown/_export?format=csv")
    assert resp.status_code == 404


# --- authz ---


def test_export_requires_list_permission(app):
    _seed_widgets(app, 2)
    _grant(app, set())
    resp = _client(app).get("/api/v1/admin/export_widgets/_export?format=csv")
    assert resp.status_code == 403


def test_export_does_not_collide_with_crud_dynamic_path(app):
    """The export route must win over /{resource}/{id} — proves install order."""
    _seed_widgets(app, 1)
    _grant(app, {"admin.export_widgets.list"})
    # Hitting _export with the integer-looking id resolver would 404 if CRUD
    # caught it first; we want a CSV response.
    resp = _client(app).get("/api/v1/admin/export_widgets/_export?format=csv")
    assert resp.status_code == 200


# --- search + cap ---


def test_export_applies_search_filter(app):
    _seed_widgets(app, 5)
    _grant(app, {"admin.export_widgets.list"})
    resp = _client(app).get("/api/v1/admin/export_widgets/_export?format=csv&search=widget-002")
    assert resp.status_code == 200
    rows = _parse_csv(resp.text)
    assert len(rows) == 1
    assert rows[0]["name"] == "widget-002"


def test_export_respects_limit_param(app):
    _seed_widgets(app, 5)
    _grant(app, {"admin.export_widgets.list"})
    resp = _client(app).get("/api/v1/admin/export_widgets/_export?format=csv&limit=2")
    assert resp.status_code == 200
    rows = _parse_csv(resp.text)
    assert len(rows) == 2


def test_export_caps_oversized_limit(app):
    _grant(app, {"admin.export_widgets.list"})
    resp = _client(app).get(
        f"/api/v1/admin/export_widgets/_export?format=csv&limit={MAX_EXPORT_ROWS * 10}"
    )
    # Cap is silent — request succeeds, just returns fewer rows than asked.
    assert resp.status_code == 200


# --- audit ---


def test_export_writes_audit_row(app):
    _seed_widgets(app, 2)
    _grant(app, {"admin.export_widgets.list"})
    resp = _client(app).get("/api/v1/admin/export_widgets/_export?format=csv")
    assert resp.status_code == 200

    runtime = app.state.adminfoundry

    async def _fetch():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.action == EXPORT_AUDIT_ACTION)
            )
            return list(result.scalars().all())

    rows = asyncio.run(_fetch())
    assert len(rows) == 1
    assert rows[0].resource == "export_widgets"
    assert rows[0].changes["rows"] == 2
    assert rows[0].changes["format"] == "csv"


# --- selection-based export ---


def _seeded_widget_ids(app, count: int) -> list[int]:
    _seed_widgets(app, count)
    runtime = app.state.adminfoundry

    async def _q():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            rows = (await session.execute(select(Widget).order_by(Widget.id))).scalars().all()
            return [w.id for w in rows]

    return asyncio.run(_q())


def test_export_with_ids_returns_only_selected_rows(app):
    ids = _seeded_widget_ids(app, 5)
    _grant(app, {"admin.export_widgets.list"})
    target = [ids[0], ids[2], ids[4]]
    query = "format=csv&" + "&".join(f"ids={i}" for i in target)
    resp = _client(app).get(f"/api/v1/admin/export_widgets/_export?{query}")
    assert resp.status_code == 200
    rows = _parse_csv(resp.text)
    assert [int(r["id"]) for r in rows] == target


def test_export_with_ids_ignores_search(app):
    ids = _seeded_widget_ids(app, 5)
    _grant(app, {"admin.export_widgets.list"})
    # Selection takes priority — search filter that would normally exclude
    # widget-000 is dropped on the floor when ids= is present.
    query = f"format=csv&ids={ids[0]}&search=widget-004"
    resp = _client(app).get(f"/api/v1/admin/export_widgets/_export?{query}")
    rows = _parse_csv(resp.text)
    assert len(rows) == 1
    assert rows[0]["name"] == "widget-000"


def test_export_with_unknown_ids_returns_empty(app):
    _seeded_widget_ids(app, 3)
    _grant(app, {"admin.export_widgets.list"})
    # Valid id shape (int) but no row matches → 0 rows, still 200.
    resp = _client(app).get(
        "/api/v1/admin/export_widgets/_export?format=csv&ids=99999"
    )
    assert resp.status_code == 200
    assert _parse_csv(resp.text) == []


def test_export_with_invalid_id_shape_returns_400(app):
    _seeded_widget_ids(app, 3)
    _grant(app, {"admin.export_widgets.list"})
    # Widget.id is Integer — a non-numeric id fails coercion.
    resp = _client(app).get(
        "/api/v1/admin/export_widgets/_export?format=csv&ids=not-an-int"
    )
    assert resp.status_code == 400


def test_export_audit_records_selection_count(app):
    ids = _seeded_widget_ids(app, 4)
    _grant(app, {"admin.export_widgets.list"})
    resp = _client(app).get(
        f"/api/v1/admin/export_widgets/_export?format=csv&ids={ids[0]}&ids={ids[1]}"
    )
    assert resp.status_code == 200

    runtime = app.state.adminfoundry

    async def _fetch():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            return list(
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == EXPORT_AUDIT_ACTION)
                    )
                ).scalars().all()
            )

    rows = asyncio.run(_fetch())
    assert len(rows) == 1
    assert rows[0].changes["selected_ids"] == 2
    assert rows[0].changes["rows"] == 2


# --- XLSX export ---


def test_export_xlsx_returns_workbook(app):
    openpyxl = pytest.importorskip("openpyxl")
    _seed_widgets(app, 3)
    _grant(app, {"admin.export_widgets.list"})
    resp = _client(app).get("/api/v1/admin/export_widgets/_export?format=xlsx")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert 'filename="export_widgets.xlsx"' in resp.headers["content-disposition"]

    wb = openpyxl.load_workbook(io.BytesIO(resp.content), read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == ("id", "name", "color")
    assert len(rows) == 4  # header + 3 data
    assert [r[1] for r in rows[1:]] == ["widget-000", "widget-001", "widget-002"]


def test_export_xlsx_audit_records_format(app):
    pytest.importorskip("openpyxl")
    _seed_widgets(app, 1)
    _grant(app, {"admin.export_widgets.list"})
    resp = _client(app).get("/api/v1/admin/export_widgets/_export?format=xlsx")
    assert resp.status_code == 200

    runtime = app.state.adminfoundry

    async def _fetch():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            return list(
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == EXPORT_AUDIT_ACTION)
                    )
                ).scalars().all()
            )

    rows = asyncio.run(_fetch())
    assert any(r.changes["format"] == "xlsx" for r in rows)


# --- CSV import ---


def _csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def _upload(app, filename: str, data: bytes, mime: str):
    return _client(app).post(
        "/api/v1/admin/export_widgets/_import",
        files={"file": (filename, data, mime)},
    )


def _count_widgets(app) -> int:
    runtime = app.state.adminfoundry

    async def _q():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            return (await session.execute(select(Widget))).scalars().all()

    return len(asyncio.run(_q()))


def test_csv_import_creates_records(app):
    _grant(app, {"admin.export_widgets.create"})
    payload = _csv_bytes(
        ["name", "color"],
        [["alpha", "red"], ["beta", "blue"]],
    )
    resp = _upload(app, "widgets.csv", payload, "text/csv")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"created": 2, "failed": 0, "total": 2, "errors": []}
    assert _count_widgets(app) == 2


def test_csv_import_requires_create_permission(app):
    _grant(app, {"admin.export_widgets.list"})  # missing .create
    payload = _csv_bytes(["name"], [["alpha"]])
    resp = _upload(app, "widgets.csv", payload, "text/csv")
    assert resp.status_code == 403


def test_csv_import_rejects_unknown_extension(app):
    _grant(app, {"admin.export_widgets.create"})
    resp = _upload(app, "widgets.txt", b"name\nalpha\n", "text/plain")
    assert resp.status_code == 400


def test_csv_import_reports_per_row_errors(app):
    _grant(app, {"admin.export_widgets.create"})
    payload = _csv_bytes(
        ["name", "color", "unknown_column"],
        [["alpha", "red", "garbage"]],
    )
    resp = _upload(app, "widgets.csv", payload, "text/csv")
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 0
    assert body["failed"] == 1
    assert body["errors"][0]["row"] == 1
    assert "unknown" in body["errors"][0]["error"].lower()
    # Nothing should have been persisted.
    assert _count_widgets(app) == 0


def test_csv_import_partial_success(app):
    _grant(app, {"admin.export_widgets.create"})
    # First row good, second row has an unknown field, third row good again.
    payload = _csv_bytes(
        ["name", "color", "junk"],
        [
            ["alpha", "red", ""],   # junk is empty → normalized away → ok
            ["beta", "blue", "x"],  # junk has value → unknown field → fails
            ["gamma", "green", ""],  # ok
        ],
    )
    resp = _upload(app, "widgets.csv", payload, "text/csv")
    body = resp.json()
    assert body["created"] == 2
    assert body["failed"] == 1
    assert _count_widgets(app) == 2


def test_csv_import_rejects_protected_field(app):
    _grant(app, {"admin.export_widgets.create"})
    payload = _csv_bytes(
        ["name", "api_secret"],
        [["alpha", "leak"]],
    )
    resp = _upload(app, "widgets.csv", payload, "text/csv")
    body = resp.json()
    assert body["created"] == 0
    assert body["failed"] == 1
    assert "api_secret" in body["errors"][0]["error"]
    assert _count_widgets(app) == 0


def test_csv_import_caps_rows(app):
    _grant(app, {"admin.export_widgets.create"})
    rows = [[f"w{i}", "x"] for i in range(MAX_IMPORT_ROWS + 5)]
    payload = _csv_bytes(["name", "color"], rows)
    resp = _upload(app, "widgets.csv", payload, "text/csv")
    assert resp.status_code == 413


def test_csv_import_writes_audit_row(app):
    _grant(app, {"admin.export_widgets.create"})
    payload = _csv_bytes(["name"], [["one"], ["two"]])
    resp = _upload(app, "widgets.csv", payload, "text/csv")
    assert resp.status_code == 200

    runtime = app.state.adminfoundry

    async def _fetch():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            return list(
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == IMPORT_AUDIT_ACTION)
                    )
                ).scalars().all()
            )

    rows = asyncio.run(_fetch())
    assert len(rows) == 1
    assert rows[0].changes == {
        "created": 2,
        "failed": 0,
        "format": "csv",
        "filename": "widgets.csv",
    }


# --- XLSX import ---


def test_xlsx_import_creates_records(app):
    openpyxl = pytest.importorskip("openpyxl")
    _grant(app, {"admin.export_widgets.create"})
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["name", "color"])
    ws.append(["alpha", "red"])
    ws.append(["beta", "blue"])
    buf = io.BytesIO()
    wb.save(buf)

    resp = _upload(
        app,
        "widgets.xlsx",
        buf.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 2
    assert body["failed"] == 0
    assert _count_widgets(app) == 2


def test_xlsx_import_skips_blank_rows(app):
    openpyxl = pytest.importorskip("openpyxl")
    _grant(app, {"admin.export_widgets.create"})
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["name", "color"])
    ws.append(["alpha", "red"])
    ws.append([None, None])  # blank row — should be skipped
    ws.append(["beta", None])
    buf = io.BytesIO()
    wb.save(buf)

    resp = _upload(
        app,
        "widgets.xlsx",
        buf.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    body = resp.json()
    assert body["created"] == 2
    assert body["failed"] == 0
