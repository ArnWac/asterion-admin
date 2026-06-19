"""B3 integration: AdminPolicy decisions in the CRUD service path.

Validates:
* Default policy (``None``) means "no extra checks" — every operation
  succeeds when permission keys allow it.
* A policy that denies ``can_create`` blocks the POST with 403 before
  any data is written.
* Per-object policies (``can_view_object`` / ``can_update_object`` /
  ``can_delete_object``) fire after the row is fetched, before the
  hooks/mutation.
* Async policy can hit the DB session (e.g. "user must share a team
  with the row's owner").
* ``ctx=None`` skips every policy call — legacy callers unaffected.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from asterion.admin.context import AdminContext
from asterion.admin.policy import AdminPolicy
from asterion.crud.services import (
    create_record,
    delete_record,
    list_records,
    read_record,
    update_record,
)
from asterion.providers.base import AdminPrincipal
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Note(_Base):
    __tablename__ = "b3_notes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(100), nullable=False)
    owner_id = Column(String(36), nullable=False)


class _NoteAdmin(ModelAdmin):
    model = _Note
    readonly_fields = ["id"]


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


def _ctx(user_id: str = "alice") -> AdminContext:
    return AdminContext(
        request=None,
        principal=AdminPrincipal(id=user_id),
        tenant=None,
    )


# ---------------------------------------------------------------------------
# Default policy = None — every op succeeds
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_policy_attribute_means_no_extra_checks(db_session):
    """Admins without a ``policy`` attribute (most of the codebase
    today) must keep working exactly like before B3."""
    admin = _NoteAdmin()
    assert admin.policy is None
    result = await create_record(
        db_session, admin, {"title": "Hi", "owner_id": "alice"}, ctx=_ctx()
    )
    assert result["title"] == "Hi"


# ---------------------------------------------------------------------------
# Default AdminPolicy = always True
# ---------------------------------------------------------------------------


class _DefaultPolicyAdmin(ModelAdmin):
    model = _Note
    readonly_fields = ["id"]
    policy = AdminPolicy()


@pytest.mark.anyio
async def test_default_policy_allows_everything(db_session):
    admin = _DefaultPolicyAdmin()
    created = await create_record(
        db_session, admin, {"title": "Hi", "owner_id": "alice"}, ctx=_ctx()
    )
    rid = str(created["id"])
    assert (await read_record(db_session, admin, rid, ctx=_ctx()))["title"] == "Hi"
    assert (await update_record(db_session, admin, rid, {"title": "Bye"}, ctx=_ctx()))[
        "title"
    ] == "Bye"
    assert await delete_record(db_session, admin, rid, ctx=_ctx())


# ---------------------------------------------------------------------------
# can_create denial → 403, no row written
# ---------------------------------------------------------------------------


class _NoCreatePolicy(AdminPolicy):
    async def can_create(self, ctx):
        return False


class _NoCreateAdmin(ModelAdmin):
    model = _Note
    readonly_fields = ["id"]
    policy = _NoCreatePolicy()


@pytest.mark.anyio
async def test_can_create_denial_raises_403(db_session):
    admin = _NoCreateAdmin()
    with pytest.raises(HTTPException) as exc:
        await create_record(db_session, admin, {"title": "x", "owner_id": "u"}, ctx=_ctx())
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_can_create_denial_writes_no_row(db_session):
    """When create is denied, the DB row must not exist. Verifies the
    policy check runs BEFORE the INSERT / hooks."""
    admin = _NoCreateAdmin()
    with pytest.raises(HTTPException):
        await create_record(db_session, admin, {"title": "x", "owner_id": "u"}, ctx=_ctx())
    # Use the underlying session to count rows directly — we can't
    # use list_records because that itself goes through can_view_model.
    from sqlalchemy import select

    result = await db_session.execute(select(_Note))
    assert result.scalars().all() == []


# ---------------------------------------------------------------------------
# can_view_object denial
# ---------------------------------------------------------------------------


class _OwnerOnlyPolicy(AdminPolicy):
    """Realistic example: only the row's owner may view / update /
    delete it. Used for ownership-scoped admins."""

    async def can_view_object(self, obj, ctx):
        return obj.owner_id == ctx.principal.id

    async def can_update_object(self, obj, ctx):
        return obj.owner_id == ctx.principal.id

    async def can_delete_object(self, obj, ctx):
        return obj.owner_id == ctx.principal.id


class _OwnerOnlyAdmin(ModelAdmin):
    model = _Note
    readonly_fields = ["id"]
    policy = _OwnerOnlyPolicy()


@pytest.mark.anyio
async def test_owner_can_view_own_row(db_session):
    admin = _OwnerOnlyAdmin()
    created = await create_record(
        db_session, admin, {"title": "x", "owner_id": "alice"}, ctx=_ctx("alice")
    )
    result = await read_record(db_session, admin, str(created["id"]), ctx=_ctx("alice"))
    assert result["title"] == "x"


@pytest.mark.anyio
async def test_non_owner_blocked_from_reading_row(db_session):
    admin = _OwnerOnlyAdmin()
    created = await create_record(
        db_session, admin, {"title": "x", "owner_id": "alice"}, ctx=_ctx("alice")
    )
    with pytest.raises(HTTPException) as exc:
        await read_record(db_session, admin, str(created["id"]), ctx=_ctx("bob"))
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_non_owner_blocked_from_updating(db_session):
    admin = _OwnerOnlyAdmin()
    created = await create_record(
        db_session, admin, {"title": "x", "owner_id": "alice"}, ctx=_ctx("alice")
    )
    with pytest.raises(HTTPException) as exc:
        await update_record(db_session, admin, str(created["id"]), {"title": "y"}, ctx=_ctx("bob"))
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_non_owner_blocked_from_deleting(db_session):
    admin = _OwnerOnlyAdmin()
    created = await create_record(
        db_session, admin, {"title": "x", "owner_id": "alice"}, ctx=_ctx("alice")
    )
    with pytest.raises(HTTPException) as exc:
        await delete_record(db_session, admin, str(created["id"]), ctx=_ctx("bob"))
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Policy runs BEFORE before_delete hook
# ---------------------------------------------------------------------------


class _TracingAdmin(ModelAdmin):
    """Records hook invocations so we can prove the policy runs first."""

    model = _Note
    readonly_fields = ["id"]
    policy = _OwnerOnlyPolicy()

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def before_delete(self, obj, ctx):
        self.calls.append("before_delete")

    async def after_delete(self, obj, ctx):
        self.calls.append("after_delete")


@pytest.mark.anyio
async def test_policy_denial_skips_hooks(db_session):
    """If the policy denies, the lifecycle hooks must NOT fire. The
    user-facing contract is that hooks observe permitted operations
    only."""
    admin = _TracingAdmin()
    created = await create_record(
        db_session, admin, {"title": "x", "owner_id": "alice"}, ctx=_ctx("alice")
    )
    with pytest.raises(HTTPException):
        await delete_record(db_session, admin, str(created["id"]), ctx=_ctx("bob"))
    assert admin.calls == []


# ---------------------------------------------------------------------------
# can_view_model resource-level gate
# ---------------------------------------------------------------------------


class _HiddenModelPolicy(AdminPolicy):
    async def can_view_model(self, ctx):
        return False


class _HiddenAdmin(ModelAdmin):
    model = _Note
    policy = _HiddenModelPolicy()


@pytest.mark.anyio
async def test_can_view_model_denial_blocks_list(db_session):
    admin = _HiddenAdmin()
    with pytest.raises(HTTPException) as exc:
        await list_records(db_session, admin, ctx=_ctx())
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Async policy hitting the session
# ---------------------------------------------------------------------------


class _DbAwarePolicy(AdminPolicy):
    """Decides based on a DB query — proves async policies work the
    way real-world apps need them to.

    Holds a session reference at construction time. In production the
    policy would resolve a session out of ``ctx.request.app.state``
    or a dependency injector; for the test we wire it directly to
    keep the surface minimal.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def can_update_object(self, obj, ctx):
        from sqlalchemy import select

        result = await self._session.execute(
            select(_Note).where(_Note.id == obj.id, _Note.owner_id == ctx.principal.id)
        )
        return result.scalar_one_or_none() is not None


@pytest.mark.anyio
async def test_async_policy_can_query_database(db_session):
    """Real-world policies will inspect related rows; verify the
    plumbing works for async DB access from within a policy method."""

    class _DbAwareAdmin(ModelAdmin):
        model = _Note
        readonly_fields = ["id"]
        policy = _DbAwarePolicy(db_session)

    admin = _DbAwareAdmin()
    created = await create_record(db_session, admin, {"title": "x", "owner_id": "alice"})
    result = await update_record(
        db_session, admin, str(created["id"]), {"title": "y"}, ctx=_ctx("alice")
    )
    assert result["title"] == "y"

    with pytest.raises(HTTPException) as exc:
        await update_record(db_session, admin, str(created["id"]), {"title": "z"}, ctx=_ctx("bob"))
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Legacy callers: ctx=None skips policy
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_legacy_caller_without_ctx_skips_policy(db_session):
    """A test or background job that passes no ctx must not have its
    create blocked by a denying policy — the policy stays opt-in."""
    admin = _NoCreateAdmin()
    result = await create_record(db_session, admin, {"title": "x", "owner_id": "u"})
    assert result["title"] == "x"
