"""NoCreateDeletePolicy + built-in global admins (v0.1.33).

The built-in ``User`` / ``Tenant`` admins are *editable* but their row
lifecycle belongs to a dedicated path (invite / provisioning), so they use
:class:`NoCreateDeletePolicy`: list / read / update stay open, create + delete
403 at the route and are hidden in the contract. ``ImpersonationLog`` is fully
read-only. ``totp_secret`` is globally protected so it never reaches a client.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from asterion.admin.context import AdminContext
from asterion.admin.policy import NoCreateDeletePolicy, ReadOnlyPolicy
from asterion.builtins.admin import ImpersonationLogAdmin, TenantAdmin, UserAdmin
from asterion.contract.service import build_model_contract
from asterion.crud.router import _require_resource_permission
from asterion.providers.base import AdminPrincipal, AdminTenant
from asterion.registry import ModelAdmin


def _ctx() -> AdminContext:
    return AdminContext(
        request=None,
        principal=AdminPrincipal(id="root", email="root@example.com", is_superadmin=True),
        tenant=None,
        permissions=frozenset({"admin.*"}),
    )


# --- policy semantics ------------------------------------------------------


def test_policy_denies_create():
    assert asyncio.run(NoCreateDeletePolicy().can_create(_ctx())) is False


def test_policy_denies_delete():
    assert asyncio.run(NoCreateDeletePolicy().can_delete_object(object(), _ctx())) is False


def test_policy_allows_update_and_read():
    policy = NoCreateDeletePolicy()
    assert asyncio.run(policy.can_update_object(object(), _ctx())) is True
    assert asyncio.run(policy.can_view_object(object(), _ctx())) is True
    assert asyncio.run(policy.can_view_model(_ctx())) is True


# --- built-in admin wiring -------------------------------------------------


def test_user_admin_is_update_only():
    assert isinstance(UserAdmin.policy, NoCreateDeletePolicy)


def test_tenant_admin_is_update_only():
    assert isinstance(TenantAdmin.policy, NoCreateDeletePolicy)


def test_impersonation_admin_is_read_only():
    assert isinstance(ImpersonationLogAdmin.policy, ReadOnlyPolicy)


def test_global_builtin_admins_are_superadmin_only():
    assert UserAdmin.superadmin_only is True
    assert TenantAdmin.superadmin_only is True
    assert ImpersonationLogAdmin.superadmin_only is True


# --- superadmin_only enforcement -------------------------------------------


def _tenant_owner_ctx() -> AdminContext:
    """A tenant owner: not superadmin, but holds admin.* inside a tenant."""
    return AdminContext(
        request=None,
        principal=AdminPrincipal(id="owner", email="owner@acme.test", is_superadmin=False),
        tenant=AdminTenant(id="11111111-1111-1111-1111-111111111111", slug="acme"),
        permissions=frozenset({"admin.*"}),
    )


def test_superadmin_only_blocks_tenant_owner_despite_admin_wildcard():
    """A tenant owner's ``admin.*`` would match ``admin.users.list``; the
    superadmin_only scope must still 403 — no cross-tenant read of public.users."""
    with pytest.raises(HTTPException) as exc:
        _require_resource_permission(_tenant_owner_ctx(), UserAdmin(), "list")
    assert exc.value.status_code == 403


def test_superadmin_only_allows_superadmin():
    ctx = AdminContext(
        request=None,
        principal=AdminPrincipal(id="root", email="root@x.test", is_superadmin=True),
        tenant=AdminTenant(id="22222222-2222-2222-2222-222222222222", slug="acme"),
        permissions=frozenset({"admin.*"}),
    )
    # Must not raise.
    _require_resource_permission(ctx, UserAdmin(), "list")


def test_non_superadmin_only_admin_unaffected():
    """A normal (non-superadmin_only) admin still authorizes a tenant owner with
    the matching key — the new scope must not regress the default path."""

    class _Thing:
        __tablename__ = "things"

    class _ThingAdmin(ModelAdmin):
        model = _Thing

    # Must not raise: admin.* matches admin.things.list, superadmin_only is False.
    _require_resource_permission(_tenant_owner_ctx(), _ThingAdmin(), "list")


def test_tenant_admin_slug_and_schema_are_readonly():
    assert "slug" in TenantAdmin.readonly_fields
    assert "schema_name" in TenantAdmin.readonly_fields


# --- contract capabilities -------------------------------------------------


def test_update_only_contract_hides_create_and_delete_keeps_update():
    """The contract reports update=True but create/delete=False even when the
    caller holds admin.* — UI shows Edit, hides New/Delete."""
    contract = build_model_contract(UserAdmin(), permissions=frozenset({"admin.*"}))
    assert contract.capabilities.create is False
    assert contract.capabilities.delete is False
    assert contract.capabilities.update is True


def test_read_only_contract_zeroes_all_writes():
    contract = build_model_contract(ImpersonationLogAdmin(), permissions=frozenset({"admin.*"}))
    assert contract.capabilities.create is False
    assert contract.capabilities.update is False
    assert contract.capabilities.delete is False


# --- secret protection -----------------------------------------------------


def test_totp_secret_is_globally_protected():
    """The 2FA shared secret must never leak through generic serialization."""
    from asterion.security.protected_fields import DEFAULT_PROTECTED_FIELDS

    assert "totp_secret" in DEFAULT_PROTECTED_FIELDS
    # And it is invisible (HIDDEN) in the User contract, not merely read-only.
    contract = build_model_contract(UserAdmin(), permissions=frozenset({"admin.*"}))
    field_names = {f.name for f in contract.fields}
    assert "totp_secret" not in field_names
    assert "hashed_password" not in field_names
