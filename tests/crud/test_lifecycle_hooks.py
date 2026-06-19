"""B2 integration: CRUD services call lifecycle hooks in order.

Verifies:
* Hook firing order for create/update/delete.
* ``before_validate``/``before_create``/``before_update`` can mutate
  the payload that lands in the DB.
* ``validate_create``/``validate_update`` can raise to reject the
  whole operation.
* ``after_*`` hooks see the persisted obj after ``flush()``.
* ``ctx=None`` skips every hook (regression guard for non-HTTP
  callers in tests / background jobs).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from asterion.admin.context import AdminContext
from asterion.crud.services import (
    create_record,
    delete_record,
    update_record,
)
from asterion.providers.base import AdminPrincipal
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Widget(_Base):
    __tablename__ = "b2_widgets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    color = Column(String(20), nullable=True)


class _TracingAdmin(ModelAdmin):
    """ModelAdmin that records the firing order of every lifecycle
    hook so the tests can assert on the exact sequence."""

    model = _Widget
    readonly_fields = ["id"]

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def before_validate(self, data, ctx):
        self.calls.append("before_validate")
        return data

    async def validate_create(self, data, ctx):
        self.calls.append("validate_create")

    async def before_create(self, data, ctx):
        self.calls.append("before_create")
        return data

    async def after_create(self, obj, ctx):
        self.calls.append("after_create")

    async def validate_update(self, obj, data, ctx):
        self.calls.append("validate_update")

    async def before_update(self, obj, data, ctx):
        self.calls.append("before_update")
        return data

    async def after_update(self, obj, changes, ctx):
        self.calls.append("after_update")

    async def before_delete(self, obj, ctx):
        self.calls.append("before_delete")

    async def after_delete(self, obj, ctx):
        self.calls.append("after_delete")


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
# Create
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_hook_order(db_session: AsyncSession):
    admin = _TracingAdmin()
    await create_record(db_session, admin, {"name": "Hi"}, ctx=_ctx())
    assert admin.calls == [
        "before_validate",
        "validate_create",
        "before_create",
        "after_create",
    ]


@pytest.mark.anyio
async def test_create_no_ctx_skips_hooks(db_session: AsyncSession):
    """Regression guard: legacy callers (tests, jobs) that pass no ctx
    must not have hooks fire — otherwise they crash on AttributeError
    when their stub admin classes don't implement them."""
    admin = _TracingAdmin()
    result = await create_record(db_session, admin, {"name": "Hi"})
    assert result["name"] == "Hi"
    assert admin.calls == []


class _StampingAdmin(ModelAdmin):
    """Realistic before_create that supplies a server-side default."""

    model = _Widget

    async def before_create(self, data, ctx):
        out = dict(data)
        out.setdefault("color", "auto")
        return out


@pytest.mark.anyio
async def test_before_create_mutation_lands_in_db(db_session: AsyncSession):
    admin = _StampingAdmin()
    created = await create_record(db_session, admin, {"name": "Hi"}, ctx=_ctx())
    assert created["color"] == "auto"


class _BeforeValidateAdmin(ModelAdmin):
    """Mutates the raw payload BEFORE the schema clean. The cleaned
    output must reflect the mutation."""

    model = _Widget

    async def before_validate(self, data, ctx):
        out = dict(data)
        out["name"] = out["name"].upper()
        return out


@pytest.mark.anyio
async def test_before_validate_runs_before_schema_clean(db_session: AsyncSession):
    admin = _BeforeValidateAdmin()
    created = await create_record(db_session, admin, {"name": "lower"}, ctx=_ctx())
    assert created["name"] == "LOWER"


class _RejectingAdmin(ModelAdmin):
    model = _Widget

    async def validate_create(self, data, ctx):
        if data.get("name") == "forbidden":
            raise ValueError("blacklisted name")


@pytest.mark.anyio
async def test_validate_create_can_reject(db_session: AsyncSession):
    admin = _RejectingAdmin()
    with pytest.raises(ValueError, match="blacklisted"):
        await create_record(db_session, admin, {"name": "forbidden"}, ctx=_ctx())


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_hook_order(db_session: AsyncSession):
    admin = _TracingAdmin()
    created = await create_record(db_session, admin, {"name": "Hi"}, ctx=_ctx())
    admin.calls.clear()  # drop the create-phase trail

    await update_record(
        db_session,
        admin,
        str(created["id"]),
        {"name": "Bye"},
        ctx=_ctx(),
    )
    assert admin.calls == [
        "before_validate",
        "validate_update",
        "before_update",
        "after_update",
    ]


class _UpdateMutatingAdmin(ModelAdmin):
    model = _Widget

    async def before_update(self, obj, data, ctx):
        out = dict(data)
        if "name" in out:
            out["name"] = out["name"].upper()
        return out


@pytest.mark.anyio
async def test_before_update_mutation_lands_in_db(db_session: AsyncSession):
    admin = _UpdateMutatingAdmin()
    created = await create_record(db_session, admin, {"name": "lower"})
    result = await update_record(
        db_session, admin, str(created["id"]), {"name": "still lower"}, ctx=_ctx()
    )
    assert result["name"] == "STILL LOWER"


class _UpdateRejectingAdmin(ModelAdmin):
    model = _Widget

    async def validate_update(self, obj, data, ctx):
        if data.get("name") == "forbidden":
            raise ValueError("blacklisted update")


@pytest.mark.anyio
async def test_validate_update_can_reject(db_session: AsyncSession):
    admin = _UpdateRejectingAdmin()
    created = await create_record(db_session, admin, {"name": "ok"})
    with pytest.raises(ValueError, match="blacklisted update"):
        await update_record(
            db_session, admin, str(created["id"]), {"name": "forbidden"}, ctx=_ctx()
        )


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_hook_order(db_session: AsyncSession):
    admin = _TracingAdmin()
    created = await create_record(db_session, admin, {"name": "Hi"}, ctx=_ctx())
    admin.calls.clear()

    await delete_record(db_session, admin, str(created["id"]), ctx=_ctx())
    assert admin.calls == ["before_delete", "after_delete"]


class _DeleteGuardAdmin(ModelAdmin):
    model = _Widget

    async def before_delete(self, obj, ctx):
        if obj.name == "protected":
            raise PermissionError("cannot delete protected widgets")


@pytest.mark.anyio
async def test_before_delete_can_block_deletion(db_session: AsyncSession):
    admin = _DeleteGuardAdmin()
    created = await create_record(db_session, admin, {"name": "protected"})
    with pytest.raises(PermissionError, match="cannot delete"):
        await delete_record(db_session, admin, str(created["id"]), ctx=_ctx())


# ---------------------------------------------------------------------------
# after_* fires AFTER flush
# ---------------------------------------------------------------------------


class _CheckPersistedAdmin(ModelAdmin):
    """``after_create`` reads obj.id, which is only populated after
    flush. If after_create ran before flush, this would be None."""

    model = _Widget

    def __init__(self) -> None:
        super().__init__()
        self.captured_id: int | None = None

    async def after_create(self, obj, ctx):
        self.captured_id = obj.id


@pytest.mark.anyio
async def test_after_create_sees_persisted_id(db_session: AsyncSession):
    admin = _CheckPersistedAdmin()
    await create_record(db_session, admin, {"name": "Hi"}, ctx=_ctx())
    assert admin.captured_id is not None
    assert admin.captured_id > 0


class _CheckChangesAdmin(ModelAdmin):
    """``after_update``'s ``changes`` arg must be the post-before_update
    payload (the dict that was actually written)."""

    model = _Widget

    def __init__(self) -> None:
        super().__init__()
        self.last_changes: dict | None = None

    async def before_update(self, obj, data, ctx):
        out = dict(data)
        # Hook adds a side-effect field; after_update must see it.
        out["color"] = "stamped"
        return out

    async def after_update(self, obj, changes, ctx):
        self.last_changes = dict(changes)


@pytest.mark.anyio
async def test_after_update_sees_post_before_update_changes(db_session: AsyncSession):
    admin = _CheckChangesAdmin()
    created = await create_record(db_session, admin, {"name": "Hi"})
    await update_record(db_session, admin, str(created["id"]), {"name": "Bye"}, ctx=_ctx())
    assert admin.last_changes is not None
    assert admin.last_changes["color"] == "stamped"
    assert admin.last_changes["name"] == "Bye"
