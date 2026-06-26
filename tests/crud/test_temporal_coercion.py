"""ISO strings for Date/DateTime/Time columns are cast, not a driver 500.

The write path filters field names but not column *types*, so an ISO-8601
string for a temporal column (e.g. ``employment_start = "2024-02-01"``) used to
reach asyncpg untouched and surface as a 500 ``DatatypeMismatchError`` ("column
is of type date but expression is of type character varying"). The admin UI
legitimately sends these inputs as strings, so the save must succeed;
``coerce_temporal_fields`` casts them and reports unparseable values as a 422.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Column, Date, DateTime, Integer, String, Time
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.crud.payload import coerce_temporal_fields
from asterion.models.base import GlobalModel
from tests._helpers import make_admin_principal, make_admin_tenant, override_admin_context


class _Base(DeclarativeBase):
    pass


class _Shift(_Base):
    __tablename__ = "shift_temporal_fixture"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    employment_start: Mapped[dt.date | None] = mapped_column(Date)
    occurred_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    clock_in: Mapped[dt.time | None] = mapped_column(Time)


def test_casts_iso_strings_for_each_temporal_type():
    cleaned = {
        "employment_start": "2024-02-01",
        "occurred_at": "2024-02-01T08:30:00+00:00",
        "clock_in": "08:30:00",
    }
    coerce_temporal_fields(cleaned, _Shift)
    assert cleaned["employment_start"] == dt.date(2024, 2, 1)
    assert cleaned["occurred_at"] == dt.datetime(
        2024, 2, 1, 8, 30, tzinfo=dt.UTC
    )
    assert cleaned["clock_in"] == dt.time(8, 30)


def test_accepts_trailing_z_datetime():
    # The shape ``new Date(...).toISOString()`` emits from the admin UI.
    cleaned = {"occurred_at": "2024-02-01T08:30:00Z"}
    coerce_temporal_fields(cleaned, _Shift)
    assert cleaned["occurred_at"] == dt.datetime(
        2024, 2, 1, 8, 30, tzinfo=dt.UTC
    )


def test_tolerates_datetime_string_in_date_column():
    cleaned = {"employment_start": "2024-02-01T08:30:00"}
    coerce_temporal_fields(cleaned, _Shift)
    assert cleaned["employment_start"] == dt.date(2024, 2, 1)


def test_unparseable_value_is_422():
    with pytest.raises(HTTPException) as exc:
        coerce_temporal_fields({"employment_start": "not-a-date"}, _Shift)
    assert exc.value.status_code == 422
    assert "employment_start" in exc.value.detail["fields"]


def test_empty_string_becomes_none():
    # A cleared form field — must not reach the driver as "".
    cleaned = {"employment_start": "   "}
    coerce_temporal_fields(cleaned, _Shift)
    assert cleaned["employment_start"] is None


def test_passes_through_non_strings_and_non_temporal_columns():
    already = dt.date(2024, 2, 1)
    cleaned = {"employment_start": already, "name": "2024-02-01"}
    coerce_temporal_fields(cleaned, _Shift)
    # Already-parsed object untouched; a String column is never coerced.
    assert cleaned["employment_start"] is already
    assert cleaned["name"] == "2024-02-01"


# --- end-to-end CRUD over HTTP ---------------------------------------------
#
# The original 500 reproduces even on SQLite: SQLAlchemy's ``Date`` bind
# processor calls ``.isoformat()`` on the value, so a raw string never reaches
# the column type as the right Python object. The fix makes the PATCH succeed
# with a parsed ``date`` and rejects garbage with a 422 (not a 500).


class Booking(_Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    employment_start = Column(Date, nullable=True)


class BookingAdmin(ModelAdmin):
    model = Booking
    list_display = ["id", "name", "employment_start"]


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'temporal.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-temporal-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: reg.register(BookingAdmin),
    )
    runtime = application.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
            await conn.run_sync(_Base.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add(Booking(name="Anna"))

    asyncio.run(_setup())
    override_admin_context(
        application,
        principal=make_admin_principal(email="user@example.com"),
        tenant=make_admin_tenant("acme"),
        permissions=frozenset({"admin.bookings.read", "admin.bookings.update"}),
    )
    yield application
    asyncio.run(runtime.db.dispose())


def test_patch_with_iso_date_string_succeeds(app):
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.patch("/api/v1/admin/bookings/1", json={"employment_start": "2024-02-01"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["employment_start"] == "2024-02-01"


def test_patch_with_garbage_date_string_is_422(app):
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.patch("/api/v1/admin/bookings/1", json={"employment_start": "not-a-date"})
    assert resp.status_code == 422, resp.text
    names = {f["name"] for f in resp.json()["error"]["fields"]}
    assert "employment_start" in names
