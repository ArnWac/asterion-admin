"""B1 lifecycle hooks: signature + default behavior on ModelAdmin.

Validates:
* All nine hooks exist on the base class with no-op defaults.
* Data-shaped hooks (``before_validate``, ``before_create``,
  ``before_update``) return the input unchanged.
* Side-effect hooks (``after_*``, ``before_delete``, ``validate_*``)
  return None and raise nothing.
* Hooks are awaitable coroutines.
* Subclasses can override.

B1 does NOT wire hooks into the router. That happens in B2 — these
tests pin the shape so the router work is testable.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from asterion.admin.context import AdminContext
from asterion.providers.base import AdminPrincipal
from asterion.registry import ModelAdmin

HOOKS_DATA_INOUT = ("before_validate", "before_create", "before_update")
HOOKS_VOID = (
    "validate_create",
    "validate_update",
    "after_create",
    "after_update",
    "after_delete",
    "before_delete",
)
ALL_HOOKS = HOOKS_DATA_INOUT + HOOKS_VOID


def _ctx() -> AdminContext:
    """Minimal stub AdminContext for hook tests. The default hooks
    don't read it, so the placeholder values are fine."""
    return AdminContext(
        request=None,
        principal=AdminPrincipal(id="u1"),
        tenant=None,
    )


class _BareAdmin(ModelAdmin):
    """No model required for hook surface tests — the hooks operate on
    the data dict / obj args, not the model itself."""


def test_all_lifecycle_hooks_present_on_base_class():
    """Pin the public surface — adding a hook is fine, removing one is
    a breaking change for downstream subclasses."""
    for name in ALL_HOOKS:
        assert hasattr(ModelAdmin, name), f"missing hook: {name}"
        attr = getattr(ModelAdmin, name)
        assert callable(attr), f"{name} must be callable"


def test_all_hooks_are_coroutine_functions():
    """Routers will ``await`` these — define them async even when the
    default body is a no-op, so apps that override don't have to
    re-declare ``async``."""
    admin = _BareAdmin()
    for name in ALL_HOOKS:
        assert inspect.iscoroutinefunction(getattr(admin, name)), name


@pytest.mark.parametrize("hook", HOOKS_DATA_INOUT)
def test_data_inout_hooks_return_input_unchanged(hook):
    admin = _BareAdmin()
    payload = {"title": "x", "n": 1}
    if hook == "before_validate":
        result = asyncio.run(admin.before_validate(payload, _ctx()))
    elif hook == "before_create":
        result = asyncio.run(admin.before_create(payload, _ctx()))
    else:
        # before_update has a different signature: (obj, data, ctx)
        result = asyncio.run(admin.before_update(object(), payload, _ctx()))
    assert result == payload
    # The default does not deep-copy — apps that need immutability
    # opt in themselves. Pin the current behavior so a future
    # "helpful" copy doesn't surprise consumers.
    assert result is payload


def test_validate_create_default_returns_none():
    admin = _BareAdmin()
    assert asyncio.run(admin.validate_create({"a": 1}, _ctx())) is None


def test_validate_update_default_returns_none():
    admin = _BareAdmin()
    assert asyncio.run(admin.validate_update(object(), {"a": 1}, _ctx())) is None


def test_before_delete_default_returns_none():
    admin = _BareAdmin()
    assert asyncio.run(admin.before_delete(object(), _ctx())) is None


def test_after_create_default_returns_none():
    admin = _BareAdmin()
    assert asyncio.run(admin.after_create(object(), _ctx())) is None


def test_after_update_default_returns_none():
    admin = _BareAdmin()
    assert asyncio.run(admin.after_update(object(), {"a": 1}, _ctx())) is None


def test_after_delete_default_returns_none():
    admin = _BareAdmin()
    assert asyncio.run(admin.after_delete(object(), _ctx())) is None


# ---------------------------------------------------------------------------
# Subclass override
# ---------------------------------------------------------------------------


class _StampedAdmin(ModelAdmin):
    """Realistic-ish subclass: a ``before_create`` that stamps a
    tenant id onto the payload. Used to verify overrides work and
    that the framework's no-op defaults don't get in the way."""

    async def before_create(self, data, ctx):
        out = dict(data)
        if ctx.tenant is not None:
            out["tenant_id"] = ctx.tenant.id
        return out


def test_subclass_can_override_hook():
    admin = _StampedAdmin()
    from asterion.providers.base import AdminTenant

    ctx = AdminContext(
        request=None,
        principal=AdminPrincipal(id="u1"),
        tenant=AdminTenant(id="t-42", slug="acme"),
    )
    result = asyncio.run(admin.before_create({"title": "Hi"}, ctx))
    assert result == {"title": "Hi", "tenant_id": "t-42"}


class _GuardedAdmin(ModelAdmin):
    """A ``before_delete`` that refuses deletion of system rows.
    Mirrors the existing ``is_system`` guard pattern."""

    async def before_delete(self, obj, ctx):
        if getattr(obj, "is_system", False):
            raise RuntimeError("system rows cannot be deleted")


def test_subclass_override_can_raise():
    admin = _GuardedAdmin()
    system_obj = type("Obj", (), {"is_system": True})()
    with pytest.raises(RuntimeError, match="system rows"):
        asyncio.run(admin.before_delete(system_obj, _ctx()))

    # Non-system row passes through.
    normal_obj = type("Obj", (), {"is_system": False})()
    assert asyncio.run(admin.before_delete(normal_obj, _ctx())) is None
