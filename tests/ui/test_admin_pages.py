"""Custom Admin Pages — registry, navigation mirror, and serving (Roadmap 5.6).

Covers the full chain the feature needs to actually work:

* the registry's validation + freeze invariants,
* ``mirror_pages_into_navigation`` turning permission-bearing pages into
  sidebar nav items under the reserved ``_pages/`` prefix,
* an extension registering a page via ``register_admin_pages`` and the
  resulting page being served (shell + js_module) without colliding with
  the dynamic ``/{resource}`` CRUD route,
* the page appearing in ``/_navigation`` only for principals that hold
  its permission.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from asterion import CoreAdminConfig, create_admin
from asterion.extensions.base import AdminExtension
from asterion.extensions.errors import RegistryFrozenError
from asterion.ui.admin_pages import (
    AdminPage,
    AdminPageRegistry,
    mirror_pages_into_navigation,
)
from asterion.ui.navigation import NavigationRegistry
from tests._helpers import (
    make_admin_principal,
    make_admin_tenant,
    override_admin_context,
)

# ---------------------------------------------------------------------------
# Registry invariants
# ---------------------------------------------------------------------------


def test_register_and_get():
    reg = AdminPageRegistry()
    page = AdminPage(id="reports", label="Reports", js_module="/admin/static/reports.js")
    reg.register(page)
    assert reg.get("reports") is page
    assert "reports" in reg
    assert len(reg) == 1
    assert reg.all() == (page,)


def test_duplicate_id_rejected():
    reg = AdminPageRegistry()
    reg.register(AdminPage(id="x", label="X", js_module="/x.js"))
    with pytest.raises(ValueError):
        reg.register(AdminPage(id="x", label="X2", js_module="/x2.js"))


@pytest.mark.parametrize("bad", ["Reports", "1bad", "has space", "_leading", ""])
def test_invalid_id_rejected(bad):
    reg = AdminPageRegistry()
    with pytest.raises(ValueError):
        reg.register(AdminPage(id=bad, label="L", js_module="/m.js"))


def test_missing_label_or_module_rejected():
    reg = AdminPageRegistry()
    with pytest.raises(ValueError):
        reg.register(AdminPage(id="a", label="", js_module="/m.js"))
    with pytest.raises(ValueError):
        reg.register(AdminPage(id="b", label="L", js_module=""))


def test_freeze_blocks_register():
    reg = AdminPageRegistry()
    reg.freeze()
    assert reg.is_frozen
    with pytest.raises(RegistryFrozenError):
        reg.register(AdminPage(id="a", label="L", js_module="/m.js"))


# ---------------------------------------------------------------------------
# Navigation mirror
# ---------------------------------------------------------------------------


def test_mirror_adds_nav_item_for_pages_with_permission():
    pages = AdminPageRegistry()
    pages.register(
        AdminPage(
            id="reports",
            label="Reports",
            js_module="/r.js",
            permission="admin.reports.view",
        )
    )
    nav = NavigationRegistry()
    mirror_pages_into_navigation(pages, nav, ui_path="/admin")

    items = nav.all()
    assert len(items) == 1
    assert items[0].id == "page.reports"
    assert items[0].path == "/admin/_pages/reports"
    assert items[0].permission == "admin.reports.view"


def test_mirror_skips_pages_without_permission():
    pages = AdminPageRegistry()
    pages.register(AdminPage(id="public", label="Public", js_module="/p.js"))
    nav = NavigationRegistry()
    mirror_pages_into_navigation(pages, nav, ui_path="/admin")
    assert nav.all() == ()


# ---------------------------------------------------------------------------
# End-to-end via create_admin + an extension
# ---------------------------------------------------------------------------


class _ReportsExtension(AdminExtension):
    name = "reports_ext"

    def register_permissions(self, registry):
        registry.register("admin.reports.view")

    def register_admin_pages(self, registry):
        registry.register(
            AdminPage(
                id="reports",
                label="Reports",
                js_module="/admin/static/reports.js",
                permission="admin.reports.view",
            )
        )


@pytest.fixture
def app(tmp_path):
    return create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'pages.db'}",
            secret_key="test-pages-secret",
            enable_multi_tenant=False,
            enable_builtin_admins=False,
        ),
        extensions=[_ReportsExtension()],
    )


def test_page_route_serves_shell_with_module(app):
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/admin/_pages/reports")
    assert resp.status_code == 200
    body = resp.text
    # Served as the page view, with the slug and js_module injected so the
    # SPA host can dynamically import the module.
    assert 'data-view="page"' in body
    assert 'data-page-id="reports"' in body
    assert "/admin/static/reports.js" in body
    # Must NOT have been swallowed by the dynamic /{resource} list route.
    assert 'data-view="list"' not in body


def test_unknown_page_returns_404(app):
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/admin/_pages/nope")
    assert resp.status_code == 404


def test_admin_pages_registry_frozen_after_setup(app):
    assert app.state.asterion.admin_pages.is_frozen is True


def test_page_in_navigation_when_permitted(app):
    override_admin_context(
        app,
        principal=make_admin_principal(email="alice@example.com"),
        tenant=make_admin_tenant("acme"),
        permissions=frozenset({"admin.reports.view"}),
    )
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/admin/_navigation")
    assert resp.status_code == 200
    paths = [i["path"] for i in resp.json()["items"]]
    assert "/admin/_pages/reports" in paths


def test_page_absent_from_navigation_without_permission(app):
    override_admin_context(
        app,
        principal=make_admin_principal(email="bob@example.com"),
        tenant=make_admin_tenant("acme"),
        permissions=frozenset(),
    )
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/admin/_navigation")
    assert resp.status_code == 200
    paths = [i["path"] for i in resp.json()["items"]]
    assert "/admin/_pages/reports" not in paths
