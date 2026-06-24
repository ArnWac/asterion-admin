"""Singleton admin — exactly one (tenant-scoped) row, as a settings page (v0.1.34).

``ModelAdmin.singleton = True`` turns a resource into a "one row per tenant"
settings/profile page. The framework:

* allows create only while the (tenant-scoped) table is empty and blocks delete
  — a 403 at the route, mirrored in ``capabilities.create`` / ``delete`` so the
  UI hides the controls;
* stamps ``singleton: true`` on the contract so the UI renders a settings page
  (nav jumps straight into the single row's detail);
* counts rows through the request session, so independence is per-tenant on
  schema-per-tenant Postgres — never a DB constraint;
* yields to an explicitly set ``policy`` (singleton only supplies the default
  create/delete behavior).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from asterion.admin.context import AdminContext
from asterion.admin.policy import AdminPolicy, ReadOnlyPolicy
from asterion.contract.service import build_model_contract
from asterion.crud.services import create_record, delete_record
from asterion.providers.base import AdminPrincipal, AdminTenant
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class OrgProfile(_Base):
    __tablename__ = "org_profiles"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)


class OrgProfileWithPolicy(_Base):
    __tablename__ = "org_profiles_policy"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)


class _AllowAll(AdminPolicy):
    """Default-allow policy — every capability stays open."""


class OrgProfileAdmin(ModelAdmin):
    model = OrgProfile
    singleton = True
    list_display = ["id", "name"]


class OrgProfilePolicyAdmin(ModelAdmin):
    """Singleton AND an explicit policy — the policy must win."""

    model = OrgProfileWithPolicy
    singleton = True
    policy = _AllowAll()


def _ctx() -> AdminContext:
    return AdminContext(
        request=None,
        principal=AdminPrincipal(id="root", email="root@example.com", is_superadmin=True),
        tenant=AdminTenant(id="11111111-1111-1111-1111-111111111111", slug="acme"),
        permissions=frozenset({"admin.*"}),
    )


# --- contract meta ---------------------------------------------------------


def test_contract_marks_singleton():
    contract = build_model_contract(OrgProfileAdmin(), permissions=frozenset({"admin.*"}))
    assert contract.singleton is True


def test_contract_non_singleton_defaults_false():
    class _Plain(ModelAdmin):
        model = OrgProfile

    contract = build_model_contract(_Plain(), permissions=frozenset({"admin.*"}))
    assert contract.singleton is False


# --- capabilities ----------------------------------------------------------


def test_capabilities_create_allowed_when_empty():
    """0 rows (singleton_full=False) → New button stays available."""
    contract = build_model_contract(
        OrgProfileAdmin(), permissions=frozenset({"admin.*"}), singleton_full=False
    )
    assert contract.capabilities.create is True
    assert contract.capabilities.update is True
    # Delete is always hidden for a singleton.
    assert contract.capabilities.delete is False


def test_capabilities_create_hidden_when_row_exists():
    """1 row (singleton_full=True) → New button hidden, edit stays."""
    contract = build_model_contract(
        OrgProfileAdmin(), permissions=frozenset({"admin.*"}), singleton_full=True
    )
    assert contract.capabilities.create is False
    assert contract.capabilities.update is True
    assert contract.capabilities.delete is False


def test_explicit_policy_takes_precedence_in_capabilities():
    """A singleton WITH a custom (allow-all) policy must not have its delete
    forced off by the singleton default — the policy owns the decision."""
    contract = build_model_contract(
        OrgProfilePolicyAdmin(), permissions=frozenset({"admin.*"}), singleton_full=True
    )
    assert contract.singleton is True
    assert contract.capabilities.create is True  # policy allows; singleton default skipped
    assert contract.capabilities.delete is True


def test_read_only_policy_still_wins_on_singleton():
    """ReadOnlyPolicy on a singleton zeroes everything (policy precedence)."""

    class _RO(ModelAdmin):
        model = OrgProfile
        singleton = True
        policy = ReadOnlyPolicy()

    contract = build_model_contract(_RO(), permissions=frozenset({"admin.*"}))
    assert contract.capabilities.create is False
    assert contract.capabilities.update is False
    assert contract.capabilities.delete is False


# --- service-level enforcement (real session) ------------------------------


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'singleton.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_allowed_when_empty_then_blocked(factory):
    async with factory() as session:
        async with session.begin():
            row = await create_record(session, OrgProfileAdmin(), {"name": "Acme"}, ctx=_ctx())
    assert row["name"] == "Acme"

    # Second create — a row already exists → 403.
    async with factory() as session:
        with pytest.raises(HTTPException) as exc:
            async with session.begin():
                await create_record(session, OrgProfileAdmin(), {"name": "Second"}, ctx=_ctx())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_blocked(factory):
    async with factory() as session:
        async with session.begin():
            row = await create_record(session, OrgProfileAdmin(), {"name": "Acme"}, ctx=_ctx())

    async with factory() as session:
        with pytest.raises(HTTPException) as exc:
            async with session.begin():
                await delete_record(session, OrgProfileAdmin(), str(row["id"]), ctx=_ctx())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_explicit_policy_lets_second_create_and_delete_through(factory):
    """An allow-all policy on a singleton disables the singleton create/delete
    guard entirely — proving precedence at the enforcement layer too."""
    admin = OrgProfilePolicyAdmin()
    async with factory() as session:
        async with session.begin():
            await create_record(session, admin, {"name": "One"}, ctx=_ctx())
        async with session.begin():
            # No 403 despite a row already existing.
            row2 = await create_record(session, admin, {"name": "Two"}, ctx=_ctx())
        async with session.begin():
            result = await delete_record(session, admin, str(row2["id"]), ctx=_ctx())
    assert result == {"deleted": True}


@pytest.mark.asyncio
async def test_singleton_count_is_per_session(tmp_path):
    """The guard counts through the request session: a full table in one
    session (tenant A) does not block create in a second, empty session
    (tenant B). On schema-per-tenant Postgres these are two ``search_path``
    scopes; here, two independent databases stand in for them."""
    engines = {}
    factories = {}
    for key in ("a", "b"):
        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / f'{key}.db'}")
        async with eng.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
        engines[key] = eng
        factories[key] = async_sessionmaker(eng, expire_on_commit=False)
    try:
        # Tenant A gets its single row.
        async with factories["a"]() as session:
            async with session.begin():
                await create_record(session, OrgProfileAdmin(), {"name": "A"}, ctx=_ctx())
        # Tenant B is still empty → create is allowed.
        async with factories["b"]() as session:
            async with session.begin():
                row = await create_record(session, OrgProfileAdmin(), {"name": "B"}, ctx=_ctx())
        assert row["name"] == "B"
    finally:
        for eng in engines.values():
            await eng.dispose()
