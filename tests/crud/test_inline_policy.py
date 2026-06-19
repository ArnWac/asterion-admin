"""Roadmap 2.2 / Gap-Analysis §4 — per-inline AdminPolicy enforcement.

Inline children now carry their own optional ``policy``. The inline
writer consults it per row:

* create → ``policy.can_create(ctx)``
* update → ``policy.can_update_object(child, ctx)``
* delete → ``policy.can_delete_object(child, ctx)``

``policy=None`` (the default) inherits the parent's gate — the parent
admin's policy already decided whether the whole save runs, so a child
without its own policy adds no restriction. ``ctx=None`` skips inline
policy entirely (legacy / non-HTTP callers).
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
from asterion.admin.policy import AdminPolicy
from asterion.crud.services import create_record, update_record
from asterion.providers.base import AdminPrincipal
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Order(_Base):
    __tablename__ = "ip_orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    customer = Column(String(100), nullable=False)


class _Line(_Base):
    __tablename__ = "ip_lines"
    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("ip_orders.id"), nullable=False)
    sku = Column(String(50), nullable=False)
    locked = Column(Integer, nullable=False, default=0)


@pytest_asyncio.fixture()
async def db_session():
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


def _ctx(role: str = "user") -> AdminContext:
    return AdminContext(
        request=None,
        principal=AdminPrincipal(id=role),
        tenant=None,
        roles=frozenset({role}),
    )


# ---------------------------------------------------------------------------
# No inline policy → parent gate only (baseline)
# ---------------------------------------------------------------------------


class _PlainLineInline(InlineAdmin):
    model = _Line
    fk_name = "order_id"
    fields = ["sku", "locked"]


class _PlainOrderAdmin(ModelAdmin):
    model = _Order
    readonly_fields = ["id"]
    inlines = [_PlainLineInline]


@pytest.mark.anyio
async def test_inline_without_policy_allows_writes(db_session):
    """The default ``policy=None`` adds no restriction — inline writes
    succeed under the parent's gate."""
    admin = _PlainOrderAdmin()
    result = await create_record(
        db_session,
        admin,
        {"customer": "Alice", "inlines": {"ip_lines": [{"sku": "A", "locked": 0}]}},
        ctx=_ctx(),
    )
    rows = (
        (await db_session.execute(select(_Line).where(_Line.order_id == result["id"])))
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.anyio
async def test_inline_policy_default_attr_is_none():
    assert _PlainLineInline().policy is None


# ---------------------------------------------------------------------------
# can_create denial on inline rows
# ---------------------------------------------------------------------------


class _NoCreateLinePolicy(AdminPolicy):
    async def can_create(self, ctx):
        return "manager" in ctx.roles


class _NoCreateLineInline(InlineAdmin):
    model = _Line
    fk_name = "order_id"
    fields = ["sku", "locked"]
    policy = _NoCreateLinePolicy()


class _NoCreateOrderAdmin(ModelAdmin):
    model = _Order
    readonly_fields = ["id"]
    inlines = [_NoCreateLineInline]


@pytest.mark.anyio
async def test_inline_create_denied_by_policy(db_session):
    admin = _NoCreateOrderAdmin()
    with pytest.raises(HTTPException) as exc:
        await create_record(
            db_session,
            admin,
            {"customer": "Bob", "inlines": {"ip_lines": [{"sku": "X", "locked": 0}]}},
            ctx=_ctx("user"),
        )
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_inline_create_allowed_for_privileged_role(db_session):
    admin = _NoCreateOrderAdmin()
    result = await create_record(
        db_session,
        admin,
        {"customer": "Bob", "inlines": {"ip_lines": [{"sku": "X", "locked": 0}]}},
        ctx=_ctx("manager"),
    )
    rows = (
        (await db_session.execute(select(_Line).where(_Line.order_id == result["id"])))
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.anyio
async def test_inline_create_denial_rolls_back_parent(db_session):
    """The inline policy denial raises inside the same transaction as
    the parent insert — pinning that the parent doesn't survive a
    rejected child (savepoint isolation, same as the C2 rollback
    contract)."""
    admin = _NoCreateOrderAdmin()
    sp = await db_session.begin_nested()
    raised = False
    try:
        await create_record(
            db_session,
            admin,
            {"customer": "Bob", "inlines": {"ip_lines": [{"sku": "X", "locked": 0}]}},
            ctx=_ctx("user"),
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
# can_update_object / can_delete_object on inline rows
# ---------------------------------------------------------------------------


class _LockGuardPolicy(AdminPolicy):
    """A line with locked=1 may neither be updated nor deleted."""

    async def can_update_object(self, obj, ctx):
        return getattr(obj, "locked", 0) == 0

    async def can_delete_object(self, obj, ctx):
        return getattr(obj, "locked", 0) == 0


class _GuardedLineInline(InlineAdmin):
    model = _Line
    fk_name = "order_id"
    fields = ["sku", "locked"]
    policy = _LockGuardPolicy()


class _GuardedOrderAdmin(ModelAdmin):
    model = _Order
    readonly_fields = ["id"]
    inlines = [_GuardedLineInline]


async def _seed_order_with_line(db_session, locked: int) -> tuple[int, int]:
    order = _Order(customer="C")
    db_session.add(order)
    await db_session.flush()
    await db_session.refresh(order)
    line = _Line(order_id=order.id, sku="S", locked=locked)
    db_session.add(line)
    await db_session.flush()
    await db_session.refresh(line)
    return order.id, line.id


@pytest.mark.anyio
async def test_inline_update_denied_for_locked_row(db_session):
    admin = _GuardedOrderAdmin()
    order_id, line_id = await _seed_order_with_line(db_session, locked=1)
    with pytest.raises(HTTPException) as exc:
        await update_record(
            db_session,
            admin,
            str(order_id),
            {"inlines": {"ip_lines": [{"id": line_id, "sku": "EDITED"}]}},
            ctx=_ctx(),
        )
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_inline_update_allowed_for_unlocked_row(db_session):
    admin = _GuardedOrderAdmin()
    order_id, line_id = await _seed_order_with_line(db_session, locked=0)
    await update_record(
        db_session,
        admin,
        str(order_id),
        {"inlines": {"ip_lines": [{"id": line_id, "sku": "EDITED"}]}},
        ctx=_ctx(),
    )
    line = (await db_session.execute(select(_Line).where(_Line.id == line_id))).scalars().one()
    assert line.sku == "EDITED"


@pytest.mark.anyio
async def test_inline_delete_denied_for_locked_row(db_session):
    admin = _GuardedOrderAdmin()
    order_id, line_id = await _seed_order_with_line(db_session, locked=1)
    with pytest.raises(HTTPException) as exc:
        await update_record(
            db_session,
            admin,
            str(order_id),
            {"inlines": {"ip_lines": [{"id": line_id, "_delete": True}]}},
            ctx=_ctx(),
        )
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_inline_delete_allowed_for_unlocked_row(db_session):
    admin = _GuardedOrderAdmin()
    order_id, line_id = await _seed_order_with_line(db_session, locked=0)
    await update_record(
        db_session,
        admin,
        str(order_id),
        {"inlines": {"ip_lines": [{"id": line_id, "_delete": True}]}},
        ctx=_ctx(),
    )
    remaining = (await db_session.execute(select(_Line).where(_Line.id == line_id))).scalars().all()
    assert remaining == []


# ---------------------------------------------------------------------------
# Legacy ctx=None skips inline policy
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ctx_none_skips_inline_policy(db_session):
    """A non-HTTP caller (no ctx) must not be subject to the inline
    policy — matches the contract that policy is opt-in via the
    request layer. Without this, scripts / background jobs using the
    inline wire format would be blocked by a policy meant for end
    users."""
    admin = _NoCreateOrderAdmin()
    # No ctx → can_create policy never consulted.
    result = await create_record(
        db_session,
        admin,
        {"customer": "Job", "inlines": {"ip_lines": [{"sku": "X", "locked": 0}]}},
    )
    rows = (
        (await db_session.execute(select(_Line).where(_Line.order_id == result["id"])))
        .scalars()
        .all()
    )
    assert len(rows) == 1
