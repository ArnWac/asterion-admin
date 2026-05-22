"""Tests for adminfoundry.authz.catalog — permission generation + sync."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from adminfoundry.actions import AdminAction, BulkDeleteAction
from adminfoundry.authz.catalog import (
    REGISTRY_SOURCE,
    generate_permission_keys,
    load_permission_keys,
    sync_permission_catalog,
)
from adminfoundry.models.base import GlobalModel
from adminfoundry.models.permission_catalog import PermissionCatalog
from adminfoundry.registry import AdminRegistry, ModelAdmin


class _AppBase(DeclarativeBase):
    pass


from sqlalchemy import Column, Integer, String


class Project(_AppBase):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)


class Widget(_AppBase):
    __tablename__ = "widgets"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)


class ProjectAdmin(ModelAdmin):
    model = Project


class WidgetAdmin(ModelAdmin):
    model = Widget
    actions = [BulkDeleteAction()]


class _Ping(AdminAction):
    name = "ping"
    label = "Ping"

    async def execute(self, records, session, user):
        return {"affected": len(records)}


class WidgetWithCustomAction(ModelAdmin):
    model = Widget
    actions = [BulkDeleteAction(), _Ping()]


# --- generate_permission_keys ---


def test_generate_empty_for_empty_registry():
    assert generate_permission_keys(AdminRegistry()) == set()


def test_generate_yields_five_crud_keys_per_resource():
    registry = AdminRegistry()
    registry.register(ProjectAdmin)
    keys = generate_permission_keys(registry)
    assert keys == {
        "admin.projects.list",
        "admin.projects.read",
        "admin.projects.create",
        "admin.projects.update",
        "admin.projects.delete",
    }


def test_generate_includes_declared_admin_actions():
    registry = AdminRegistry()
    registry.register(WidgetAdmin)
    keys = generate_permission_keys(registry)
    # BulkDeleteAction.name == "delete" → already in CRUD set, so 5 unique keys
    assert keys == {
        "admin.widgets.list",
        "admin.widgets.read",
        "admin.widgets.create",
        "admin.widgets.update",
        "admin.widgets.delete",
    }


def test_generate_action_with_unique_name_adds_extra_key():
    registry = AdminRegistry()
    registry.register(WidgetWithCustomAction)
    keys = generate_permission_keys(registry)
    assert "admin.widgets.ping" in keys
    assert "admin.widgets.delete" in keys  # CRUD + BulkDeleteAction merge


def test_generate_multiple_resources():
    registry = AdminRegistry()
    registry.register(ProjectAdmin)
    registry.register(WidgetAdmin)
    keys = generate_permission_keys(registry)
    assert "admin.projects.list" in keys
    assert "admin.widgets.list" in keys
    assert len(keys) == 10  # 5 + 5


def test_generate_does_not_yield_wildcards():
    registry = AdminRegistry()
    registry.register(ProjectAdmin)
    keys = generate_permission_keys(registry)
    assert "admin.*" not in keys
    assert "admin.projects.*" not in keys


# --- generate_permission_keys with PermissionRegistry (Phase 6a) ---


def test_generate_merges_extension_permission_registry():
    """Keys contributed by extensions via PermissionRegistry must show
    up in the catalog alongside the admin CRUD keys."""
    from adminfoundry.authz.registry import PermissionRegistry

    admin_reg = AdminRegistry()
    admin_reg.register(ProjectAdmin)

    perm_reg = PermissionRegistry()
    perm_reg.register("oauth.identities.list", "oauth.identities.unlink")

    keys = generate_permission_keys(admin_reg, perm_reg)
    # admin CRUD keys still present
    assert "admin.projects.list" in keys
    assert "admin.projects.delete" in keys
    # extension keys merged in
    assert "oauth.identities.list" in keys
    assert "oauth.identities.unlink" in keys


def test_generate_works_with_only_permission_registry():
    """Empty AdminRegistry + non-empty PermissionRegistry = only extension keys."""
    from adminfoundry.authz.registry import PermissionRegistry

    perm_reg = PermissionRegistry()
    perm_reg.register("oauth.identities.list")

    keys = generate_permission_keys(AdminRegistry(), perm_reg)
    assert keys == {"oauth.identities.list"}


def test_generate_unchanged_when_permission_registry_omitted():
    """Backward-compat: callers that don't pass permission_registry get
    exactly the legacy admin-derived keys."""
    registry = AdminRegistry()
    registry.register(ProjectAdmin)
    keys = generate_permission_keys(registry)
    keys_explicit = generate_permission_keys(registry, None)
    assert keys == keys_explicit


# --- sync_permission_catalog ---


@pytest_asyncio.fixture
async def session(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}"
    engine = create_async_engine(
        db_url,
        execution_options={"schema_translate_map": {"public": None}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(GlobalModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_inserts_new_keys(session):
    keys = {"admin.users.list", "admin.users.read"}
    result = await sync_permission_catalog(session, keys)
    assert result.added == 2
    assert result.removed == 0
    assert result.kept == 0

    rows = (await session.execute(select(PermissionCatalog))).scalars().all()
    assert {row.key for row in rows} == keys
    for row in rows:
        assert row.source == REGISTRY_SOURCE


@pytest.mark.asyncio
async def test_sync_is_idempotent(session):
    keys = {"admin.users.list", "admin.users.read"}
    await sync_permission_catalog(session, keys)
    result2 = await sync_permission_catalog(session, keys)
    assert result2.added == 0
    assert result2.removed == 0
    assert result2.kept == 2


@pytest.mark.asyncio
async def test_sync_categorizes_by_resource(session):
    await sync_permission_catalog(session, {"admin.projects.list"})
    row = (
        await session.execute(
            select(PermissionCatalog).where(PermissionCatalog.key == "admin.projects.list")
        )
    ).scalar_one()
    assert row.category == "projects"


@pytest.mark.asyncio
async def test_sync_prunes_stale_registry_keys(session):
    await sync_permission_catalog(session, {"admin.users.list", "admin.users.delete"})
    # Re-sync with a smaller set
    result = await sync_permission_catalog(session, {"admin.users.list"})
    assert result.added == 0
    assert result.removed == 1
    assert result.kept == 1

    remaining = (await session.execute(select(PermissionCatalog.key))).scalars().all()
    assert set(remaining) == {"admin.users.list"}


@pytest.mark.asyncio
async def test_sync_prune_false_keeps_stale(session):
    await sync_permission_catalog(session, {"admin.users.list", "admin.users.delete"})
    result = await sync_permission_catalog(
        session,
        {"admin.users.list"},
        prune=False,
    )
    assert result.removed == 0
    keys = await load_permission_keys(session)
    assert keys == {"admin.users.list", "admin.users.delete"}


@pytest.mark.asyncio
async def test_sync_only_prunes_its_own_source(session):
    # Manually insert an entry from a different source
    session.add(
        PermissionCatalog(
            key="admin.foo.bar",
            category="foo",
            source="manual",
        )
    )
    await session.flush()

    await sync_permission_catalog(
        session,
        {"admin.users.list"},
        source=REGISTRY_SOURCE,
    )
    keys = await load_permission_keys(session)
    # Manual entry survives
    assert "admin.foo.bar" in keys
    assert "admin.users.list" in keys


@pytest.mark.asyncio
async def test_sync_rejects_invalid_keys(session):
    with pytest.raises(Exception):
        await sync_permission_catalog(
            session,
            {"INVALID KEY"},
        )


@pytest.mark.asyncio
async def test_sync_end_to_end_with_registry(session):
    registry = AdminRegistry()
    registry.register(ProjectAdmin)
    registry.register(WidgetWithCustomAction)

    keys = generate_permission_keys(registry)
    result = await sync_permission_catalog(session, keys)

    assert result.added == len(keys)
    stored = await load_permission_keys(session)
    assert keys.issubset(stored)
