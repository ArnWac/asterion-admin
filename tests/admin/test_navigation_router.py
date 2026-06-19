"""HTTP integration tests for /api/v1/admin/_navigation (Phase 9).

Validates the Phase-9 promise: an extension's NavigationRegistry items
reach the wire as a per-user filtered list. Three axes are exercised:

1. **Empty state** — an app with no extensions returns ``{"items": []}``.
2. **Permission filtering** — a principal sees only the items whose
   ``permission`` key they actually hold.
3. **Superadmin bypass** — ``is_superadmin=True`` sees every registered
   item regardless of permission key (the built-in permission provider
   only grants ``admin.*``, which wouldn't otherwise match extension
   namespaces like ``oauth.identities.list``).

A 4th test guards the auth gate: anonymous callers get 401, not the
empty list — the endpoint name leaks "things exist that you might
want", so anonymous reads are not allowed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from asterion import CoreAdminConfig, create_admin
from asterion.extensions.base import AdminExtension
from asterion.security.protected_fields import reset_for_tests as reset_protected
from tests._helpers import make_admin_principal, override_admin_context


@pytest.fixture(autouse=True)
def _isolate_protected_fields():
    reset_protected()
    yield
    reset_protected()


class _NavExtension(AdminExtension):
    """Minimal extension that contributes three nav items.

    Each item lives under a distinct permission so tests can grant a
    subset and observe per-item filtering.
    """

    name = "nav_test"

    def register_navigation(self, registry) -> None:
        registry.add_item(
            id="navtest.alpha",
            label="Alpha",
            path="/admin/extensions/alpha",
            permission="navtest.alpha.view",
        )
        registry.add_item(
            id="navtest.beta",
            label="Beta",
            path="/admin/extensions/beta",
            permission="navtest.beta.view",
        )
        registry.add_item(
            id="navtest.gamma",
            label="Gamma",
            path="/admin/extensions/gamma",
            permission="navtest.gamma.view",
        )


def _config(tmp_path) -> CoreAdminConfig:
    return CoreAdminConfig(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'nav.db'}",
        secret_key="test-nav",
        enable_multi_tenant=False,
        enable_builtin_ui=False,
        enable_builtin_admins=False,
    )


# --- empty state ---


def test_navigation_empty_when_no_extensions(tmp_path):
    app = create_admin(config=_config(tmp_path))
    override_admin_context(app, principal=make_admin_principal())
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/v1/admin/_navigation")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


# --- filtering ---


def test_navigation_filters_by_permission(tmp_path):
    app = create_admin(config=_config(tmp_path), extensions=[_NavExtension()])
    override_admin_context(
        app,
        principal=make_admin_principal(),
        permissions={"navtest.alpha.view", "navtest.gamma.view"},
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/v1/admin/_navigation").json()
    ids = [it["id"] for it in body["items"]]
    assert ids == ["navtest.alpha", "navtest.gamma"]


def test_navigation_hides_all_items_for_principal_with_no_permissions(tmp_path):
    app = create_admin(config=_config(tmp_path), extensions=[_NavExtension()])
    override_admin_context(app, principal=make_admin_principal())  # no perms
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/v1/admin/_navigation").json()
    assert body == {"items": []}


def test_navigation_superadmin_sees_all_items(tmp_path):
    """Superadmins bypass the per-item permission check.

    The built-in permission provider only grants superadmins ``admin.*``,
    which would NOT match ``navtest.*.view`` under a strict permission
    match. The endpoint's ``is_superadmin`` short-circuit is what makes
    the platform-owner role usable across extension namespaces.
    """
    app = create_admin(config=_config(tmp_path), extensions=[_NavExtension()])
    override_admin_context(
        app,
        principal=make_admin_principal(is_superadmin=True),
        # Intentionally NO permissions — proving the bypass, not the match.
        permissions=frozenset(),
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/v1/admin/_navigation").json()
    ids = [it["id"] for it in body["items"]]
    assert ids == ["navtest.alpha", "navtest.beta", "navtest.gamma"]


def test_navigation_response_shape_is_id_label_path(tmp_path):
    """The wire shape is intentionally minimal — no permission key
    leaks to the client (server has already filtered)."""
    app = create_admin(config=_config(tmp_path), extensions=[_NavExtension()])
    override_admin_context(
        app,
        principal=make_admin_principal(),
        permissions={"navtest.alpha.view"},
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/v1/admin/_navigation").json()
    assert body["items"] == [
        {"id": "navtest.alpha", "label": "Alpha", "path": "/admin/extensions/alpha"}
    ]


# --- auth gate ---


def test_navigation_requires_authentication(tmp_path):
    """No principal → 401, not an empty list. The presence of items is
    itself information."""
    app = create_admin(config=_config(tmp_path), extensions=[_NavExtension()])
    # Deliberately NOT calling override_admin_context — the real
    # require_admin_context dependency rejects anonymous requests.
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/v1/admin/_navigation")
    assert resp.status_code == 401
