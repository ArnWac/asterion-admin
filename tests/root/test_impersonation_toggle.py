"""Phase C — ``enable_impersonation`` gates the impersonation route.

The superadmin impersonation endpoint (``POST {root}/impersonate``) used to be
mounted unconditionally. It's now behind ``CoreAdminConfig.enable_impersonation``
(default True, preserving the old behaviour). The users + tenants root routes
are always mounted regardless — the tenant list powers the UI tenant switcher.
"""

from __future__ import annotations

import os
from unittest import mock

from asterion import CoreAdminConfig, create_admin

ROOT = "/api/v1/root"


def _route_paths(app) -> set[str]:
    return {getattr(r, "path", None) for r in app.router.routes}


def _make_app(tmp_path, *, enable_impersonation: bool):
    return create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'imp_toggle.db'}",
            secret_key="test-imp-toggle-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
            enable_impersonation=enable_impersonation,
        )
    )


# ---------------------------------------------------------------------------
# Route gating
# ---------------------------------------------------------------------------


def test_impersonate_route_mounted_when_enabled(tmp_path):
    paths = _route_paths(_make_app(tmp_path, enable_impersonation=True))
    assert f"{ROOT}/impersonate" in paths
    # Users + tenants are always present.
    assert f"{ROOT}/tenants" in paths
    assert f"{ROOT}/users" in paths


def test_impersonate_route_absent_when_disabled(tmp_path):
    paths = _route_paths(_make_app(tmp_path, enable_impersonation=False))
    assert f"{ROOT}/impersonate" not in paths
    # Disabling impersonation must NOT drop the other root routes — the
    # tenant switcher still needs /tenants.
    assert f"{ROOT}/tenants" in paths
    assert f"{ROOT}/users" in paths


# ---------------------------------------------------------------------------
# Config flag
# ---------------------------------------------------------------------------


def test_enable_impersonation_defaults_true():
    cfg = CoreAdminConfig(database_url="sqlite+aiosqlite://", secret_key="x")
    assert cfg.enable_impersonation is True


def test_enable_impersonation_explicit_false():
    cfg = CoreAdminConfig(
        database_url="sqlite+aiosqlite://", secret_key="x", enable_impersonation=False
    )
    assert cfg.enable_impersonation is False


def test_enable_impersonation_in_safe_dict():
    cfg = CoreAdminConfig(database_url="sqlite+aiosqlite://", secret_key="x")
    assert cfg.to_safe_dict()["enable_impersonation"] is True


def test_enable_impersonation_from_env():
    env = {
        "ASTERION_DATABASE_URL": "sqlite+aiosqlite://",
        "ASTERION_SECRET_KEY": "env-secret-not-default",
        "ASTERION_ENABLE_IMPERSONATION": "false",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        cfg = CoreAdminConfig.from_env()
    assert cfg.enable_impersonation is False
