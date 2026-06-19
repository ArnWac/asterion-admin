"""C2: transactional parent/child inline writes.

Validates:
* New inline rows are created with the parent's pk stamped onto the fk
  column.
* Update path: existing inline rows update by id.
* Delete path: ``_delete: true`` removes the child row.
* Failure inside an inline row rolls back the parent insert/update too
  (single transaction).
* Wire-format errors: unknown table, unknown columns, missing fk_name,
  max_num exceeded.
* Legacy ``ctx=None`` path still processes inlines.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Column, ForeignKey, Integer, String, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from asterion.admin import InlineAdmin
from asterion.admin.context import AdminContext
from asterion.crud.services import create_record, update_record
from asterion.providers.base import AdminPrincipal
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Order(_Base):
    __tablename__ = "c2_orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    customer = Column(String(100), nullable=False)


class _Line(_Base):
    __tablename__ = "c2_lines"
    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("c2_orders.id"), nullable=False)
    sku = Column(String(50), nullable=False)
    qty = Column(Integer, nullable=False)


class _LineInline(InlineAdmin):
    model = _Line
    fk_name = "order_id"
    fields = ["sku", "qty"]
    can_delete = True


class _OrderAdmin(ModelAdmin):
    model = _Order
    readonly_fields = ["id"]
    inlines = [_LineInline]


@pytest_asyncio.fixture()
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            yield session
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
# Create with inlines
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_with_inline_rows_stamps_fk(db_session):
    admin = _OrderAdmin()
    result = await create_record(
        db_session,
        admin,
        {
            "customer": "Alice",
            "inlines": {
                "c2_lines": [
                    {"sku": "A1", "qty": 2},
                    {"sku": "B2", "qty": 1},
                ]
            },
        },
        ctx=_ctx(),
    )
    order_id = result["id"]
    rows = (
        (await db_session.execute(select(_Line).where(_Line.order_id == order_id))).scalars().all()
    )
    assert len(rows) == 2
    assert {r.sku for r in rows} == {"A1", "B2"}
    # Response now carries the created inline rows back so the client
    # doesn't need a second GET to render them (C2+ augmentation).
    assert "inlines" in result
    assert {r["sku"] for r in result["inlines"]["c2_lines"]} == {"A1", "B2"}


@pytest.mark.anyio
async def test_create_inline_failure_rolls_back_parent(db_session):
    """An inline row with an unknown column must abort the entire
    transaction. We wrap the call in a savepoint so the rollback is
    observable from inside the outer test transaction — in production,
    ``get_async_session`` plays the same role at the request boundary.
    """
    admin = _OrderAdmin()
    sp = await db_session.begin_nested()
    raised = False
    try:
        await create_record(
            db_session,
            admin,
            {
                "customer": "Alice",
                "inlines": {
                    "c2_lines": [{"sku": "ok", "qty": 1, "ghost": 9}],
                },
            },
            ctx=_ctx(),
        )
    except HTTPException:
        raised = True
    finally:
        if sp.is_active:
            await sp.rollback()

    assert raised
    orders = (await db_session.execute(select(_Order))).scalars().all()
    assert orders == []


# ---------------------------------------------------------------------------
# Update with inlines: create / update / delete
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_inline_creates_new_row(db_session):
    admin = _OrderAdmin()
    created = await create_record(db_session, admin, {"customer": "Bob"}, ctx=_ctx())
    await update_record(
        db_session,
        admin,
        str(created["id"]),
        {
            "inlines": {
                "c2_lines": [{"sku": "NEW", "qty": 3}],
            }
        },
        ctx=_ctx(),
    )
    rows = (
        (await db_session.execute(select(_Line).where(_Line.order_id == created["id"])))
        .scalars()
        .all()
    )
    assert {r.sku for r in rows} == {"NEW"}


@pytest.mark.anyio
async def test_update_inline_with_id_updates_row(db_session):
    admin = _OrderAdmin()
    created = await create_record(
        db_session,
        admin,
        {
            "customer": "Bob",
            "inlines": {"c2_lines": [{"sku": "X", "qty": 1}]},
        },
        ctx=_ctx(),
    )
    line = (
        (await db_session.execute(select(_Line).where(_Line.order_id == created["id"])))
        .scalars()
        .one()
    )

    await update_record(
        db_session,
        admin,
        str(created["id"]),
        {"inlines": {"c2_lines": [{"id": line.id, "qty": 99}]}},
        ctx=_ctx(),
    )
    await db_session.refresh(line)
    assert line.qty == 99
    assert line.sku == "X"  # untouched


@pytest.mark.anyio
async def test_update_inline_delete_marker_removes_row(db_session):
    admin = _OrderAdmin()
    created = await create_record(
        db_session,
        admin,
        {
            "customer": "Bob",
            "inlines": {
                "c2_lines": [
                    {"sku": "keep", "qty": 1},
                    {"sku": "drop", "qty": 1},
                ]
            },
        },
        ctx=_ctx(),
    )
    drop_line = (await db_session.execute(select(_Line).where(_Line.sku == "drop"))).scalars().one()

    await update_record(
        db_session,
        admin,
        str(created["id"]),
        {"inlines": {"c2_lines": [{"id": drop_line.id, "_delete": True}]}},
        ctx=_ctx(),
    )

    remaining = (
        (await db_session.execute(select(_Line).where(_Line.order_id == created["id"])))
        .scalars()
        .all()
    )
    assert {r.sku for r in remaining} == {"keep"}


# ---------------------------------------------------------------------------
# Wire-format validation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unknown_inline_table_rejected(db_session):
    admin = _OrderAdmin()
    with pytest.raises(HTTPException) as exc:
        await create_record(
            db_session,
            admin,
            {"customer": "Alice", "inlines": {"ghost_table": []}},
            ctx=_ctx(),
        )
    assert exc.value.status_code == 422


@pytest.mark.anyio
async def test_unknown_inline_field_rejected(db_session):
    admin = _OrderAdmin()
    with pytest.raises(HTTPException) as exc:
        await create_record(
            db_session,
            admin,
            {
                "customer": "Alice",
                "inlines": {"c2_lines": [{"sku": "x", "qty": 1, "ghost": 9}]},
            },
            ctx=_ctx(),
        )
    assert exc.value.status_code == 422


@pytest.mark.anyio
async def test_inlines_must_be_object_not_list(db_session):
    admin = _OrderAdmin()
    with pytest.raises(HTTPException) as exc:
        await create_record(
            db_session,
            admin,
            {"customer": "Alice", "inlines": [{"sku": "x"}]},
            ctx=_ctx(),
        )
    assert exc.value.status_code == 422


@pytest.mark.anyio
async def test_inline_rows_must_be_list(db_session):
    admin = _OrderAdmin()
    with pytest.raises(HTTPException) as exc:
        await create_record(
            db_session,
            admin,
            {"customer": "Alice", "inlines": {"c2_lines": "not a list"}},
            ctx=_ctx(),
        )
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# max_num + can_delete enforcement
# ---------------------------------------------------------------------------


class _CappedInline(InlineAdmin):
    model = _Line
    fk_name = "order_id"
    fields = ["sku", "qty"]
    max_num = 2


class _CappedAdmin(ModelAdmin):
    model = _Order
    readonly_fields = ["id"]
    inlines = [_CappedInline]


@pytest.mark.anyio
async def test_max_num_enforced(db_session):
    admin = _CappedAdmin()
    with pytest.raises(HTTPException) as exc:
        await create_record(
            db_session,
            admin,
            {
                "customer": "x",
                "inlines": {
                    "c2_lines": [
                        {"sku": "a", "qty": 1},
                        {"sku": "b", "qty": 1},
                        {"sku": "c", "qty": 1},
                    ]
                },
            },
            ctx=_ctx(),
        )
    assert exc.value.status_code == 422


class _NoDeleteInline(InlineAdmin):
    model = _Line
    fk_name = "order_id"
    fields = ["sku", "qty"]
    can_delete = False


class _NoDeleteAdmin(ModelAdmin):
    model = _Order
    readonly_fields = ["id"]
    inlines = [_NoDeleteInline]


@pytest.mark.anyio
async def test_can_delete_false_blocks_inline_deletion(db_session):
    admin = _NoDeleteAdmin()
    created = await create_record(
        db_session,
        admin,
        {
            "customer": "x",
            "inlines": {"c2_lines": [{"sku": "a", "qty": 1}]},
        },
        ctx=_ctx(),
    )
    line = (await db_session.execute(select(_Line))).scalars().one()
    with pytest.raises(HTTPException) as exc:
        await update_record(
            db_session,
            admin,
            str(created["id"]),
            {"inlines": {"c2_lines": [{"id": line.id, "_delete": True}]}},
            ctx=_ctx(),
        )
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Legacy ctx=None still processes inlines
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_legacy_ctx_none_path_still_handles_inlines(db_session):
    """A non-HTTP caller (no ctx) using the inline wire format must
    still trigger the inline writer — same shape, just no hooks /
    policy."""
    admin = _OrderAdmin()
    result = await create_record(
        db_session,
        admin,
        {
            "customer": "X",
            "inlines": {"c2_lines": [{"sku": "S", "qty": 1}]},
        },
    )
    rows = (
        (await db_session.execute(select(_Line).where(_Line.order_id == result["id"])))
        .scalars()
        .all()
    )
    assert len(rows) == 1
