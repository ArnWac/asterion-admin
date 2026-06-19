"""CRUD test matrix — gap-filling (Roadmap 1.6 / Doc-2 §7).

The existing CRUD test files already cover most of the matrix:

* ``test_router_permissions.py`` (27 tests) — permission + 404 + 422
  edges per CRUD verb.
* ``test_services.py`` (5 tests) — happy paths.
* ``test_lifecycle_hooks.py`` (12 tests) — hook firing + rejection
  semantics.
* ``test_policy.py`` + ``test_field_policy.py`` (22 tests) — policy
  denial + field permissions.
* ``test_inlines.py`` (12 tests) — inline transactional behaviour.
* ``test_payload.py`` (6 tests) — clean_write_payload edge cases.

This module *only* covers the remaining matrix cells that aren't pinned
elsewhere:

* update with invalid PK shape (e.g. "not-an-int") → 422
* update of unknown record → 404
* delete with invalid PK shape → 422
* validate_create rejection rolls back the would-be parent insert
* validate_update rejection leaves the DB row untouched
* unique-constraint conflict on insert surfaces as IntegrityError
  (no silent swallow)

The goal is *coverage*, not breadth — each test corresponds to one
specific matrix cell that was previously implicit.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Column, Integer, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from asterion.admin.context import AdminContext
from asterion.crud.services import (
    create_record,
    delete_record,
    read_record,
    update_record,
)
from asterion.providers.base import AdminPrincipal
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Widget(_Base):
    __tablename__ = "matrix_widgets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sku = Column(String(40), nullable=False)
    name = Column(String(100), nullable=False)
    __table_args__ = (UniqueConstraint("sku", name="uq_matrix_widgets_sku"),)


class _WidgetAdmin(ModelAdmin):
    model = _Widget
    readonly_fields = ["id"]


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        async with s.begin():
            yield s
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.drop_all)
    await engine.dispose()


def _ctx() -> AdminContext:
    return AdminContext(
        request=None,
        principal=AdminPrincipal(id="u1"),
        tenant=None,
    )


# ---------------------------------------------------------------------------
# PK coercion edge cases on update + delete
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_with_invalid_pk_shape_returns_422(session):
    """``read`` already pins this (test_router_permissions). Update +
    delete take the same PK coercion path; pin them too so a future
    refactor can't break parity between the three verbs."""
    admin = _WidgetAdmin()
    with pytest.raises(HTTPException) as exc:
        await update_record(session, admin, "not-an-int", {"name": "x"})
    assert exc.value.status_code == 422


@pytest.mark.anyio
async def test_delete_with_invalid_pk_shape_returns_422(session):
    admin = _WidgetAdmin()
    with pytest.raises(HTTPException) as exc:
        await delete_record(session, admin, "not-an-int")
    assert exc.value.status_code == 422


@pytest.mark.anyio
async def test_update_of_unknown_record_returns_404(session):
    admin = _WidgetAdmin()
    with pytest.raises(HTTPException) as exc:
        await update_record(session, admin, "9999", {"name": "x"})
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Transaction-rollback contracts on validate_* failures
# ---------------------------------------------------------------------------


class _RejectingAdmin(ModelAdmin):
    model = _Widget
    readonly_fields = ["id"]

    async def validate_create(self, data, ctx):
        if data.get("sku") == "FORBIDDEN":
            raise ValueError("sku is on the deny list")

    async def validate_update(self, obj, data, ctx):
        if data.get("name") == "FORBIDDEN":
            raise ValueError("name is on the deny list")


@pytest.mark.anyio
async def test_validate_create_rejection_leaves_no_row(session):
    """``validate_create`` runs before INSERT — a raise must prevent
    the row from being added to the session. Pinning this so a future
    re-ordering of the hook pipeline can't accidentally INSERT first
    then validate."""
    admin = _RejectingAdmin()
    with pytest.raises(ValueError, match="deny list"):
        await create_record(
            session,
            admin,
            {"sku": "FORBIDDEN", "name": "x"},
            ctx=_ctx(),
        )
    rows = (await session.execute(select(_Widget))).scalars().all()
    assert rows == []


@pytest.mark.anyio
async def test_validate_update_rejection_leaves_row_untouched(session):
    """``validate_update`` runs AFTER fetch but BEFORE write. A raise
    must leave the persisted row unchanged. The hook receives the row
    so it can decide based on current state — but if it rejects, the
    SQL UPDATE must not happen."""
    admin = _RejectingAdmin()
    created = await create_record(session, admin, {"sku": "OK", "name": "before"})
    rid = str(created["id"])

    with pytest.raises(ValueError, match="deny list"):
        await update_record(session, admin, rid, {"name": "FORBIDDEN"}, ctx=_ctx())

    reread = await read_record(session, admin, rid)
    assert reread["name"] == "before"


# ---------------------------------------------------------------------------
# Conflict (unique constraint) — pin DB-level enforcement
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_duplicate_unique_key_surfaces_integrity_error(session):
    """A DB-level UNIQUE violation surfaces as ``IntegrityError`` from
    ``session.flush()``. The framework doesn't (yet) translate this to
    a friendly 409; pin the current behaviour so apps know what to
    expect and so a future translation lands deliberately.

    Translating to 409 is part of the Doc-2 §6 error-UX work and
    happens in a later phase; this test exists to make that work
    visible when it happens (the assertion will need updating)."""
    from sqlalchemy.exc import IntegrityError

    admin = _WidgetAdmin()
    await create_record(session, admin, {"sku": "SAME", "name": "first"})
    with pytest.raises(IntegrityError):
        await create_record(session, admin, {"sku": "SAME", "name": "second"})


# ---------------------------------------------------------------------------
# Pagination edges that weren't covered by test_router_permissions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_default_pagination_returns_envelope(session):
    """Sanity: a fresh list call without any params returns the
    envelope shape with empty items. Pins the "no records, no error"
    contract because it's the first thing an empty admin sees."""
    from asterion.crud.services import list_records

    admin = _WidgetAdmin()
    result = await list_records(session, admin)
    assert result == {"items": [], "total": 0, "limit": 100, "offset": 0}


@pytest.mark.anyio
async def test_list_offset_beyond_total_returns_empty_items(session):
    """``offset=999`` on a table with 1 row → empty items + non-zero
    total. Ensures the count query doesn't get clamped by the
    pagination limit."""
    from asterion.crud.services import list_records

    admin = _WidgetAdmin()
    await create_record(session, admin, {"sku": "A", "name": "x"})
    result = await list_records(session, admin, offset=999)
    assert result["items"] == []
    assert result["total"] == 1
