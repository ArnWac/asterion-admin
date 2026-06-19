"""B4: field-level policy decisions reach serializer + schema.

Validates:
* :data:`FieldPermission.HIDDEN` removes a field from serialized
  read output AND from the create/update schema.
* :data:`FieldPermission.READ` keeps the field readable but rejects
  writes to it.
* :data:`FieldPermission.WRITE` is the default — no change.
* Field policies receive the row object for update/read and ``None``
  for create.
* ``ctx=None`` skips field policy completely (legacy callers).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from asterion.admin.context import AdminContext
from asterion.admin.policy import AdminPolicy, FieldPermission
from asterion.crud.services import (
    create_record,
    list_records,
    read_record,
    update_record,
)
from asterion.providers.base import AdminPrincipal
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Person(_Base):
    __tablename__ = "b4_people"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    salary = Column(Integer, nullable=True)
    private_note = Column(String(500), nullable=True)


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


def _ctx(role: str = "user") -> AdminContext:
    return AdminContext(
        request=None,
        principal=AdminPrincipal(id=role),
        tenant=None,
        roles=frozenset({role}),
    )


# ---------------------------------------------------------------------------
# HIDDEN drops field everywhere
# ---------------------------------------------------------------------------


class _HideSalaryPolicy(AdminPolicy):
    """Hides ``salary`` from non-managers. ``private_note`` stays
    visible for everyone — used as a control to confirm the policy
    only touches the field it names."""

    async def field_permission(self, field, obj, ctx):
        if field == "salary" and "manager" not in ctx.roles:
            return FieldPermission.HIDDEN
        return FieldPermission.WRITE


class _HideSalaryAdmin(ModelAdmin):
    model = _Person
    readonly_fields = ["id"]
    policy = _HideSalaryPolicy()


@pytest.mark.anyio
async def test_hidden_field_omitted_from_read(db_session):
    """A non-manager reading a record must not see the salary key."""
    admin = _HideSalaryAdmin()
    # Seed with a privileged ctx so we can write salary in the first place.
    created = await create_record(
        db_session,
        admin,
        {"name": "Alice", "salary": 90000, "private_note": "ok"},
        ctx=_ctx("manager"),
    )
    rid = str(created["id"])

    # Non-manager read: salary absent
    user_view = await read_record(db_session, admin, rid, ctx=_ctx("user"))
    assert "salary" not in user_view
    assert user_view["name"] == "Alice"
    assert user_view["private_note"] == "ok"


@pytest.mark.anyio
async def test_hidden_field_omitted_from_list(db_session):
    """Same rule for list pages."""
    admin = _HideSalaryAdmin()
    await create_record(
        db_session,
        admin,
        {"name": "Alice", "salary": 90000},
        ctx=_ctx("manager"),
    )

    listing = await list_records(db_session, admin, ctx=_ctx("user"))
    assert listing["items"]
    for row in listing["items"]:
        assert "salary" not in row


@pytest.mark.anyio
async def test_hidden_field_rejected_in_create(db_session):
    """Submitting a HIDDEN field on create must be rejected with 422,
    not silently dropped — the field doesn't exist for this caller."""
    admin = _HideSalaryAdmin()
    with pytest.raises(HTTPException) as exc:
        await create_record(
            db_session,
            admin,
            {"name": "Alice", "salary": 90000},
            ctx=_ctx("user"),
        )
    # clean_write_payload treats unknown fields as 422.
    assert exc.value.status_code == 422


@pytest.mark.anyio
async def test_hidden_field_rejected_in_update(db_session):
    """A non-manager cannot update salary."""
    admin = _HideSalaryAdmin()
    created = await create_record(
        db_session,
        admin,
        {"name": "Alice", "salary": 50000},
        ctx=_ctx("manager"),
    )
    rid = str(created["id"])

    with pytest.raises(HTTPException) as exc:
        await update_record(db_session, admin, rid, {"salary": 99999}, ctx=_ctx("user"))
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# READ — visible but writes rejected
# ---------------------------------------------------------------------------


class _ReadOnlyNotePolicy(AdminPolicy):
    """``private_note`` is read-only for ``observer`` role — they see
    it but cannot change it."""

    async def field_permission(self, field, obj, ctx):
        if field == "private_note" and "observer" in ctx.roles:
            return FieldPermission.READ
        return FieldPermission.WRITE


class _ReadOnlyAdmin(ModelAdmin):
    model = _Person
    readonly_fields = ["id"]
    policy = _ReadOnlyNotePolicy()


@pytest.mark.anyio
async def test_read_permission_keeps_field_visible(db_session):
    admin = _ReadOnlyAdmin()
    created = await create_record(
        db_session,
        admin,
        {"name": "Bob", "private_note": "kept secret"},
        ctx=_ctx("manager"),
    )
    rid = str(created["id"])
    view = await read_record(db_session, admin, rid, ctx=_ctx("observer"))
    assert view["private_note"] == "kept secret"


@pytest.mark.anyio
async def test_read_permission_rejects_writes(db_session):
    """Observer can read the note but a write attempt must 422."""
    admin = _ReadOnlyAdmin()
    created = await create_record(
        db_session,
        admin,
        {"name": "Bob", "private_note": "kept secret"},
        ctx=_ctx("manager"),
    )
    rid = str(created["id"])
    with pytest.raises(HTTPException) as exc:
        await update_record(
            db_session,
            admin,
            rid,
            {"private_note": "tampered"},
            ctx=_ctx("observer"),
        )
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# WRITE default — no policy effect
# ---------------------------------------------------------------------------


class _DefaultPolicyAdmin(ModelAdmin):
    model = _Person
    readonly_fields = ["id"]
    policy = AdminPolicy()


@pytest.mark.anyio
async def test_default_field_permission_is_write(db_session):
    """Stock AdminPolicy returns WRITE for every field — no behavior
    change vs. an admin without a policy."""
    admin = _DefaultPolicyAdmin()
    created = await create_record(db_session, admin, {"name": "Carol", "salary": 1}, ctx=_ctx())
    rid = str(created["id"])
    updated = await update_record(db_session, admin, rid, {"salary": 2}, ctx=_ctx())
    assert updated["salary"] == 2
    assert "salary" in updated  # not hidden


# ---------------------------------------------------------------------------
# obj=None on create, obj=row on update/read
# ---------------------------------------------------------------------------


class _ObjAwarePolicy(AdminPolicy):
    """Pins the contract: ``obj`` is None when creating, the row
    instance otherwise. Tests record what they saw."""

    def __init__(self) -> None:
        self.saw: list[tuple[str, type | None]] = []

    async def field_permission(self, field, obj, ctx):
        self.saw.append((field, type(obj) if obj is not None else None))
        return FieldPermission.WRITE


@pytest.mark.anyio
async def test_field_policy_sees_none_on_create(db_session):
    """During the create path the field policy is called twice:
    once to shape the write schema (obj=None, no row exists yet) and
    once to filter the post-insert serialized response (obj=row). At
    least one call must carry obj=None — pinning the create-schema
    contract."""
    pol = _ObjAwarePolicy()

    class _Admin(ModelAdmin):
        model = _Person
        readonly_fields = ["id"]
        policy = pol

    await create_record(db_session, _Admin(), {"name": "Dan"}, ctx=_ctx())
    assert pol.saw
    # At least one call carried obj=None — the schema-shaping pass.
    assert any(observed is None for _f, observed in pol.saw)


@pytest.mark.anyio
async def test_field_policy_sees_row_on_update(db_session):
    pol = _ObjAwarePolicy()

    class _Admin(ModelAdmin):
        model = _Person
        readonly_fields = ["id"]
        policy = pol

    admin = _Admin()
    created = await create_record(db_session, admin, {"name": "Eve"}, ctx=_ctx())
    pol.saw.clear()
    await update_record(db_session, admin, str(created["id"]), {"name": "Eve2"}, ctx=_ctx())
    # Every field call during update-schema-shaping passed a _Person row.
    assert pol.saw
    assert all(observed is _Person for _f, observed in pol.saw)


# ---------------------------------------------------------------------------
# Legacy ctx=None path is unaffected
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_legacy_ctx_none_path_ignores_field_policy(db_session):
    """A caller without ctx must not be subject to the field policy —
    matches the contract that policy is opt-in via the route layer."""
    admin = _HideSalaryAdmin()
    # No ctx → field policy never consulted, salary may be written.
    result = await create_record(db_session, admin, {"name": "Alice", "salary": 1})
    assert result["salary"] == 1
