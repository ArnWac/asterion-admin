"""SuperadminDeletablePolicy + disable_update capability marker (v0.1.50, ADR-0004).

Covers the two capability-model gaps an embedding app hit:

* ``disable_update`` lets a policy hide the Edit control independently of
  ``read_only`` (previously ``update`` hung solely off ``read_only``).
* ``SuperadminDeletablePolicy`` blocks create/update for everyone and allows
  delete only for the platform tier. Because a tenant ``owner`` also carries
  ``admin.*``, the delete is gated on the ``platform.<resource>.delete`` key
  (ADR-0004): a superadmin holds ``platform.*`` and matches it; an owner holds
  only ``admin.*`` and does not — so the contract capability and the route gate
  agree, with no visible-but-403 Delete button and no ``is_superadmin`` branch.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from asterion.admin.context import AdminContext
from asterion.admin.policy import AdminPolicy, SuperadminDeletablePolicy
from asterion.contract.service import build_model_contract
from asterion.crud.services import create_record, delete_record
from asterion.providers.base import AdminPrincipal, AdminTenant
from asterion.registry import AdminRegistry, ModelAdmin


class _Base(DeclarativeBase):
    pass


class Ledger(_Base):
    __tablename__ = "ledger"
    id = Column(Integer, primary_key=True)
    memo = Column(String(200), nullable=False)


class LedgerAdmin(ModelAdmin):
    model = Ledger
    policy = SuperadminDeletablePolicy()


# Register so the policy is bound to its resource ("ledger") — this is what lets
# the object-level delete gate build the ``platform.ledger.delete`` key. The
# policy instance is shared at class level, so binding once suffices for every
# ``LedgerAdmin()`` constructed below.
AdminRegistry().register(LedgerAdmin)


#: A superadmin's effective grant (ADR-0004): both tiers.
_SUPERADMIN_PERMS = frozenset({"admin.*", "platform.*"})
#: A tenant owner's grant: the tenant tier only.
_OWNER_PERMS = frozenset({"admin.*"})


def _superadmin_ctx() -> AdminContext:
    return AdminContext(
        request=None,
        principal=AdminPrincipal(id="root", email="root@x.test", is_superadmin=True),
        tenant=AdminTenant(id="22222222-2222-2222-2222-222222222222", slug="acme"),
        permissions=_SUPERADMIN_PERMS,
    )


def _owner_ctx() -> AdminContext:
    """Tenant owner: not superadmin but holds admin.* inside a tenant."""
    return AdminContext(
        request=None,
        principal=AdminPrincipal(id="owner", email="owner@acme.test", is_superadmin=False),
        tenant=AdminTenant(id="11111111-1111-1111-1111-111111111111", slug="acme"),
        permissions=_OWNER_PERMS,
    )


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


# --- disable_update marker -------------------------------------------------


def test_disable_update_marker_hides_edit_without_read_only():
    class _NoUpdatePolicy(AdminPolicy):
        disable_update = True

    class _Admin(ModelAdmin):
        model = Ledger
        policy = _NoUpdatePolicy()

    contract = build_model_contract(_Admin(), permissions=frozenset({"admin.*"}))
    assert contract.capabilities.update is False
    # create/delete unaffected by disable_update alone.
    assert contract.capabilities.create is True
    assert contract.capabilities.delete is True


def test_disable_update_defaults_false_is_backward_compatible():
    assert AdminPolicy().capability_flags() == (True, True, True)


# --- SuperadminDeletablePolicy capabilities --------------------------------


def test_superadmin_sees_delete_but_not_create_or_update():
    contract = build_model_contract(LedgerAdmin(), permissions=_SUPERADMIN_PERMS)
    assert contract.capabilities.create is False
    assert contract.capabilities.update is False
    assert contract.capabilities.delete is True


def test_tenant_owner_with_admin_wildcard_sees_no_delete():
    contract = build_model_contract(LedgerAdmin(), permissions=_OWNER_PERMS)
    assert contract.capabilities.create is False
    assert contract.capabilities.update is False
    assert contract.capabilities.delete is False


def test_capability_flags_shape():
    policy = SuperadminDeletablePolicy()
    assert policy.capability_flags(has_platform=True) == (False, False, True)
    assert policy.capability_flags(has_platform=False) == (False, False, False)


# --- SuperadminDeletablePolicy route gates ---------------------------------


def test_route_gate_allows_delete_for_platform_operator():
    policy = LedgerAdmin().policy  # bound to "ledger" via registration
    assert asyncio.run(policy.can_delete_object(object(), _superadmin_ctx())) is True


def test_route_gate_blocks_delete_for_tenant_owner():
    policy = LedgerAdmin().policy
    assert asyncio.run(policy.can_delete_object(object(), _owner_ctx())) is False


def test_route_gate_blocks_create_and_update_for_all():
    policy = SuperadminDeletablePolicy()
    assert asyncio.run(policy.can_create(_superadmin_ctx())) is False
    assert asyncio.run(policy.can_update_object(object(), _superadmin_ctx())) is False


def test_unbound_policy_fails_safe_on_delete():
    """A policy that was never registered has no resource to key on, so the
    delete gate denies rather than leaking access."""
    policy = SuperadminDeletablePolicy()
    assert policy.resource is None
    assert asyncio.run(policy.can_delete_object(object(), _superadmin_ctx())) is False


def test_impersonation_blocks_delete():
    """During impersonation the admin carries the impersonated tenant user's
    keys (no ``platform.*``), so the platform-only delete is blocked."""
    impersonating = AdminContext(
        request=None,
        # An impersonated principal presents as a tenant user: not superadmin.
        principal=AdminPrincipal(id="target", email="user@acme.test", is_superadmin=False),
        tenant=AdminTenant(id="11111111-1111-1111-1111-111111111111", slug="acme"),
        permissions=_OWNER_PERMS,
    )
    policy = LedgerAdmin().policy
    assert asyncio.run(policy.can_delete_object(object(), impersonating)) is False


@pytest.mark.anyio
async def test_delete_record_403s_tenant_owner(db_session):
    """End-to-end through the CRUD service: a tenant owner's delete 403s,
    matching the hidden capability."""
    created = await create_record(db_session, LedgerAdmin(), {"memo": "row"})
    with pytest.raises(HTTPException) as exc:
        await delete_record(db_session, LedgerAdmin(), str(created["id"]), ctx=_owner_ctx())
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_delete_record_allows_platform_operator(db_session):
    created = await create_record(db_session, LedgerAdmin(), {"memo": "row"})
    # Must not raise — the platform operator passes the object gate.
    await delete_record(db_session, LedgerAdmin(), str(created["id"]), ctx=_superadmin_ctx())
