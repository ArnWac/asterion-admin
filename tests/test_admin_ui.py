"""Built-in admin UI tests (fast layer).

Covers:
- route mounting when ENABLE_BUILTIN_ADMIN_UI=True (default)
- static asset serving
- all HTML shell routes return 200 with text/html
- disabling the built-in UI does not affect API routes
- renderer support matrix endpoint and structure
- accessibility attributes present in templates
- login page does not require auth
- regression: existing API routes unaffected
"""
import pytest
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport

from adminfoundry.admin.ui_renderer import get_support_matrix, RENDERER_ID, SUPPORTED_FEATURES
from adminfoundry.admin.registry import admin_site


# ---------------------------------------------------------------------------
# Renderer support matrix — unit tests
# ---------------------------------------------------------------------------

def test_support_matrix_structure():
    matrix = get_support_matrix()
    assert matrix["renderer"] == RENDERER_ID
    assert "version" in matrix
    assert "supported" in matrix
    assert isinstance(matrix["supported"], dict)


def test_support_matrix_core_features_supported():
    """Core CRUD flows must be marked supported in the baseline renderer."""
    m = SUPPORTED_FEATURES
    assert m["list"] is True
    assert m["detail"] is True
    assert m["create"] is True
    assert m["update"] is True
    assert m["search"] is True
    assert m["pagination"] is True
    assert m["readonly_fields"] is True
    assert m["protected_field_filtering"] is True


def test_support_matrix_deferred_features_not_supported():
    """Features not yet in built-in UI must be False (safe fallback)."""
    m = SUPPORTED_FEATURES
    # audit_log_view and workflow_approval remain deferred
    assert m["audit_log_view"] is False
    # bulk_actions, delete, dangerous_actions are supported
    assert m.get("unsupported_features_degrade_safely") is True


def test_support_matrix_quality_flags():
    m = SUPPORTED_FEATURES
    assert m["localization_ready"] is True
    assert m["accessibility_baseline"] is True
    assert m["unsupported_features_degrade_safely"] is True


# ---------------------------------------------------------------------------
# Route mounting helpers
# ---------------------------------------------------------------------------

def _make_app_enabled():
    """Return a fresh app with ENABLE_BUILTIN_ADMIN_UI=True."""
    import importlib
    import adminfoundry.main as m
    importlib.reload(m)
    return m.app


def _make_app_disabled():
    """Return a fresh app with ENABLE_BUILTIN_ADMIN_UI=False."""
    with patch("adminfoundry.settings.settings.ENABLE_BUILTIN_ADMIN_UI", False):
        import importlib
        import adminfoundry.main as m
        importlib.reload(m)
        return m.app


# ---------------------------------------------------------------------------
# HTML shell routes — enabled (default)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_page_accessible(client):
    resp = await client.get("/admin-ui/login")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert b"coreAdmin" in resp.content


@pytest.mark.asyncio
async def test_login_page_contains_form(client):
    resp = await client.get("/admin-ui/login")
    assert b'<form' in resp.content
    assert b'email' in resp.content
    assert b'password' in resp.content


@pytest.mark.asyncio
async def test_dashboard_page(client):
    resp = await client.get("/admin-ui/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_list_page(client):
    resp = await client.get("/admin-ui/user")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # model name in page
    assert b"user" in resp.content


@pytest.mark.asyncio
async def test_create_page(client):
    resp = await client.get("/admin-ui/user/new")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_detail_page(client):
    import uuid
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/admin-ui/user/{fake_id}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_update_page(client):
    import uuid
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/admin-ui/user/{fake_id}/edit")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_root_redirect(client):
    resp = await client.get("/admin-ui/", follow_redirects=False)
    # Returns client-side redirect HTML (JS checks localStorage token)
    assert resp.status_code == 200
    assert b"coreAdmin_access" in resp.content


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_static_css_served(client):
    resp = await client.get("/admin-ui/static/admin.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_static_js_served(client):
    resp = await client.get("/admin-ui/static/admin.js")
    assert resp.status_code == 200
    ct = resp.headers.get("content-type", "")
    assert "javascript" in ct or "text" in ct


# ---------------------------------------------------------------------------
# Renderer support matrix — HTTP endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_support_matrix_endpoint(client):
    resp = await client.get("/admin-ui/renderer/support-matrix")
    assert resp.status_code == 200
    data = resp.json()
    assert data["renderer"] == RENDERER_ID
    assert "supported" in data
    assert data["supported"]["list"] is True
    assert data["supported"]["unsupported_features_degrade_safely"] is True


# ---------------------------------------------------------------------------
# Accessibility — ARIA labels and lang attribute in templates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_page_lang_attribute(client):
    resp = await client.get("/admin-ui/login")
    assert b'lang="en"' in resp.content


@pytest.mark.asyncio
async def test_login_page_aria_attributes(client):
    resp = await client.get("/admin-ui/login")
    assert b'aria-required' in resp.content
    assert b'role="alert"' in resp.content


@pytest.mark.asyncio
async def test_list_page_aria_attributes(client):
    resp = await client.get("/admin-ui/user")
    assert b'aria-label' in resp.content
    assert b'lang="en"' in resp.content


@pytest.mark.asyncio
async def test_base_template_skip_link(client):
    resp = await client.get("/admin-ui/dashboard")
    assert b'main-content' in resp.content  # skip-link target


# ---------------------------------------------------------------------------
# Security — no protected fields in HTML shells
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_page_no_secrets(client):
    """Login shell must not embed tokens or hashes."""
    resp = await client.get("/admin-ui/login")
    assert b"hashed_password" not in resp.content
    assert b"SECRET_KEY" not in resp.content


@pytest.mark.asyncio
async def test_list_page_no_secrets(client):
    resp = await client.get("/admin-ui/user")
    assert b"hashed_password" not in resp.content
    assert b"SECRET_KEY" not in resp.content


# ---------------------------------------------------------------------------
# Template context — ui_base injected correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ui_base_in_login_template(client):
    resp = await client.get("/admin-ui/login")
    assert b"/admin-ui" in resp.content


@pytest.mark.asyncio
async def test_ui_base_in_list_template(client):
    resp = await client.get("/admin-ui/user")
    assert b"/admin-ui" in resp.content


# ---------------------------------------------------------------------------
# Disabled UI — API routes unaffected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_unaffected_when_ui_disabled(db_engine):
    """Disabling the built-in UI must not break API routes."""
    with patch("adminfoundry.settings.settings.ENABLE_BUILTIN_ADMIN_UI", False):
        import importlib
        import adminfoundry.main as m
        importlib.reload(m)
        disabled_app = m.app

    transport = ASGITransport(app=disabled_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ui_routes_absent_when_disabled(db_engine):
    """When UI disabled, /admin-ui/login should 404."""
    with patch("adminfoundry.settings.settings.ENABLE_BUILTIN_ADMIN_UI", False):
        import importlib
        import adminfoundry.main as m
        importlib.reload(m)
        disabled_app = m.app

    transport = ASGITransport(app=disabled_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/admin-ui/login")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Regression — Phase 0–6 API routes still work
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_still_works(client):
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_login_still_works(client, superadmin):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_admin_api_still_works(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "models" in resp.json()
