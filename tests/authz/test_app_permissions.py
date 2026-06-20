"""Extension-free custom permission keys via create_admin(permissions=...)
(Change 2).

An embedding app (e.g. Simpletimes) can declare its own permission keys without
writing an AdminExtension. The keys land in ``runtime.permission_registry`` and
— exactly like extension-registered keys — are merged by
``generate_permission_keys`` (which ``asterion permissions sync`` uses to fill
the PermissionCatalog).
"""

from __future__ import annotations

import pytest

from asterion import CoreAdminConfig, create_admin
from asterion.authz import generate_permission_keys
from asterion.extensions import AdminExtension
from asterion.security.validation import InvalidPermissionKeyError


def _cfg(tmp_path):
    return CoreAdminConfig(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'perms.db'}",
        secret_key="test-app-permissions",
        enable_multi_tenant=False,
        enable_builtin_ui=False,
        enable_builtin_admins=False,
    )


def _merged_keys(app):
    rt = app.state.asterion
    return generate_permission_keys(rt.registry, rt.permission_registry)


def test_permissions_list_lands_in_generated_keys(tmp_path):
    app = create_admin(
        config=_cfg(tmp_path),
        permissions=["timeclock.employee.read", "timeclock.employee.write"],
    )
    keys = _merged_keys(app)
    assert {"timeclock.employee.read", "timeclock.employee.write"} <= keys


def test_permissions_callable_form(tmp_path):
    def declare(registry):
        registry.register("timeclock.shift.read", "timeclock.shift.close")

    app = create_admin(config=_cfg(tmp_path), permissions=declare)
    assert {"timeclock.shift.read", "timeclock.shift.close"} <= _merged_keys(app)


def test_invalid_permission_key_raises_at_create(tmp_path):
    with pytest.raises(InvalidPermissionKeyError):
        create_admin(config=_cfg(tmp_path), permissions=["not a valid key!"])


def test_none_is_a_noop(tmp_path):
    # Backwards-compatible: omitting the param behaves exactly as before.
    app = create_admin(config=_cfg(tmp_path))
    # Only the auto-derived keys (none here, no admins registered) — no crash.
    assert isinstance(_merged_keys(app), set)


def test_app_and_extension_keys_merge_idempotently(tmp_path):
    """A key declared by BOTH the app and an extension is fine — the registry
    is a set, so it appears once and neither registration raises."""

    class _PermExt(AdminExtension):
        name = "permext"

        def register_permissions(self, registry):
            registry.register("timeclock.employee.read", "billing.invoice.read")

    app = create_admin(
        config=_cfg(tmp_path),
        extensions=[_PermExt()],
        permissions=["timeclock.employee.read", "timeclock.employee.write"],
    )
    keys = _merged_keys(app)
    assert {
        "timeclock.employee.read",  # declared by both app + extension
        "timeclock.employee.write",  # app only
        "billing.invoice.read",  # extension only
    } <= keys
