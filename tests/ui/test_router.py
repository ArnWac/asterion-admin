"""Smoke tests for the minimal built-in UI shell.

These verify that every route declared on /admin renders, that the static
asset mount serves admin.css/admin.js, and that the shell renders only the
two templates the plan permits (app.html, login.html). No real UI behavior
is exercised — the shell is intentionally minimal and is driven by the
contract + CRUD APIs at runtime via JavaScript.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asterion import CoreAdminConfig, create_admin

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "asterion"


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'ui.db'}"
    return create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-ui-secret",
            enable_multi_tenant=False,
            enable_builtin_admins=False,
        )
    )


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# --- routes ---


def test_admin_root_redirects_to_dashboard(client):
    resp = client.get("/admin/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"].endswith("/admin/dashboard")


def test_login_page_renders(client):
    resp = client.get("/admin/login")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert 'id="login-form"' in body
    assert "/api/v1/auth/login" not in body  # login URL is built in JS from cfg
    assert 'data-view="login"' in body
    # Login page uses the boxed standalone shell (no sidebar).
    assert 'class="login-wrap"' in body
    assert 'class="login-box"' in body
    assert 'class="sidebar"' not in body


def test_dashboard_renders_shell(client):
    resp = client.get("/admin/dashboard")
    assert resp.status_code == 200
    body = resp.text
    assert 'data-view="dashboard"' in body
    # The sidebar shell is the central UX of this refactor — assert its
    # structural anchors are present on every app page.
    assert 'class="skip-link"' in body
    assert 'class="layout"' in body
    assert 'class="sidebar"' in body
    assert 'id="sidebar-nav"' in body
    # Phase 9: extension-contributed nav items live in their own list
    # below the models. Server template must reserve the slot even when
    # no extensions are loaded — admin.js reveals it after fetching
    # /_navigation.
    assert 'id="sidebar-extensions"' in body
    assert 'id="user-ctx"' in body
    assert 'id="signout"' in body
    assert 'id="breadcrumb"' in body
    assert 'id="main-content"' in body
    # The old topbar must not return.
    assert 'class="topbar"' not in body


def test_settings_renders_shell(client):
    resp = client.get("/admin/settings")
    assert resp.status_code == 200
    assert 'data-view="settings"' in resp.text


def test_resource_list_renders_shell(client):
    resp = client.get("/admin/widgets")
    assert resp.status_code == 200
    assert 'data-view="list"' in resp.text
    assert 'data-resource="widgets"' in resp.text


def test_resource_create_renders_shell(client):
    resp = client.get("/admin/widgets/new")
    assert resp.status_code == 200
    assert 'data-view="create"' in resp.text
    assert 'data-resource="widgets"' in resp.text


def test_resource_detail_renders_shell(client):
    resp = client.get("/admin/widgets/42")
    assert resp.status_code == 200
    assert 'data-view="detail"' in resp.text
    assert 'data-record-id="42"' in resp.text


def test_resource_edit_renders_shell(client):
    resp = client.get("/admin/widgets/42/edit")
    assert resp.status_code == 200
    assert 'data-view="edit"' in resp.text


def test_resource_delete_renders_shell(client):
    resp = client.get("/admin/widgets/42/delete")
    assert resp.status_code == 200
    assert 'data-view="delete"' in resp.text


# --- OAuth fragment-redirect landing page (Phase 8b.8) ---


def test_login_complete_page_renders_standalone_shell(client):
    """Standalone page (NOT app.html) so the JS runs BEFORE the
    'no token? go to /login' redirect in admin.js — at this point in
    the OAuth flow, localStorage has no token yet."""
    resp = client.get("/admin/login-complete")
    assert resp.status_code == 200
    assert 'data-view="login-complete"' in resp.text
    # Standalone shell — no sidebar.
    assert 'class="sidebar"' not in resp.text
    assert 'class="login-wrap"' in resp.text
    # The fragment carries the JWT; this is the page that reads it.
    assert 'id="login-complete-status"' in resp.text


def test_login_complete_page_blocks_search_indexing(client):
    """The landing page never carries data on its own, but its URL is
    /admin/login-complete — exclude from search engines anyway."""
    resp = client.get("/admin/login-complete")
    assert 'name="robots"' in resp.text
    assert "noindex" in resp.text.lower()
    assert 'name="referrer"' in resp.text
    assert "no-referrer" in resp.text


def test_login_page_includes_oauth_providers_slot(client):
    """The login page reserves a slot the JS fills from
    /_login_contract — assert the slot exists on the static template
    so the JS has something to populate."""
    resp = client.get("/admin/login")
    assert 'id="oauth-providers"' in resp.text


# --- static asset mount ---


def test_static_css_served(client):
    resp = client.get("/admin/static/admin.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]


def test_static_js_served(client):
    resp = client.get("/admin/static/admin.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


def test_static_diff_module_served(client):
    """Roadmap 5.1b — the diff module the detail view dynamically
    imports must actually be reachable through the static mount."""
    resp = client.get("/admin/static/diff.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    body = resp.text
    # Pin the public surface so detail.js's named imports keep working.
    assert "looksLikeAuditDiff" in body
    assert "renderDiffTable" in body


def test_static_unknown_returns_404(client):
    resp = client.get("/admin/static/nope.js")
    assert resp.status_code == 404


# --- template surface ---


def test_only_minimal_templates_exist():
    """Guards against legacy templates returning.

    Phase 8b.8 added login_complete.html as the landing page for the
    OAuth fragment redirect — it's a sibling of login.html, NOT a
    view inside app.html (the JWT-in-fragment trick needs a standalone
    page that runs before the normal authenticated-view shell)."""
    templates_dir = PACKAGE_ROOT / "ui" / "templates"
    files = sorted(p.name for p in templates_dir.iterdir() if p.is_file())
    assert files == ["app.html", "login.html", "login_complete.html"], files


def test_no_admin_subdirectory_left():
    templates_dir = PACKAGE_ROOT / "ui" / "templates"
    assert not (templates_dir / "admin").exists()


def test_static_admin_layout():
    """The bundled UI ships as native ES modules — a single entrypoint plus
    a small set of core helpers and one file per view. Guards against random
    legacy files reappearing at the top level."""
    static_dir = PACKAGE_ROOT / "ui" / "static" / "admin"
    files = sorted(p.name for p in static_dir.iterdir() if p.is_file())
    assert files == [
        "admin.css",
        "admin.js",
        "api.js",
        "contract.js",
        "diff.js",
        "dom.js",
        "format.js",
        "impersonation.js",
        "logic.js",
    ], files
    views_dir = static_dir / "views"
    assert views_dir.is_dir(), "views/ directory must exist"
    view_files = sorted(p.name for p in views_dir.iterdir() if p.is_file())
    assert view_files == [
        "dashboard.js",
        "delete.js",
        "detail.js",
        "form.js",
        "import_modal.js",
        "list.js",
        "login.js",
        "login_complete.js",
        "page.js",
        "permission_matrix.js",
        "role_permissions.js",
        "settings.js",
    ], view_files


def test_static_view_module_served(client):
    """End-to-end check that the StaticFiles mount serves nested module files
    (not just the entrypoint), so the dynamic imports in admin.js resolve."""
    resp = client.get("/admin/static/views/dashboard.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


# --- builtin UI can be disabled ---


def test_builtin_ui_disabled(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'ui-off.db'}"
    app = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-ui-off",
            enable_multi_tenant=False,
            enable_builtin_admins=False,
            enable_builtin_ui=False,
        )
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/admin/login")
    assert resp.status_code == 404
