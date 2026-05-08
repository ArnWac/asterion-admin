"""Built-in UI completeness tests (fast layer).

Covers:
- Renderer support matrix features
- confirm_delete page renders with danger zone and confirmation button
- UIPreference schema defaults and validation
- Preference get/put API endpoints (authenticated)
- Preference isolation per user
- Preferences cannot encode security-relevant overrides
- Impersonation indicator and tenant context in base template
- Validation field-level error containers in templates
"""
import pytest
import uuid
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport

from adminfoundry.admin.ui_renderer import get_support_matrix, SUPPORTED_FEATURES, RENDERER_VERSION
from adminfoundry.admin.ui_preferences import UIPreference, get_preferences, set_preferences, clear_preferences


# ---------------------------------------------------------------------------
# Renderer support matrix — Phase 8 features now True
# ---------------------------------------------------------------------------

def test_renderer_version_is_at_least_phase8():
    # Version was 8.0 at Phase 8; later phases increment it
    major = float(RENDERER_VERSION.split(".")[0])
    assert major >= 8


def test_delete_now_supported():
    assert SUPPORTED_FEATURES["delete"] is True


def test_dangerous_actions_now_supported():
    assert SUPPORTED_FEATURES["dangerous_actions"] is True



def test_field_filters_now_supported():
    assert SUPPORTED_FEATURES["field_filters"] is True


def test_preference_persistence_supported():
    assert SUPPORTED_FEATURES["preference_persistence"] is True


def test_impersonation_state_visible_supported():
    assert SUPPORTED_FEATURES["impersonation_state_visible"] is True


def test_validation_field_level_errors_supported():
    assert SUPPORTED_FEATURES["validation_field_level_errors"] is True


def test_bulk_actions_supported_from_phase11():
    """Phase 11 added bulk_actions support."""
    assert SUPPORTED_FEATURES["bulk_actions"] is True


def test_relation_selection_supported_from_phase9():
    """Phase 9 added relation_selection support."""
    assert SUPPORTED_FEATURES["relation_selection"] is True


def test_support_matrix_endpoint_reflects_phase8(client):
    import asyncio
    async def _run():
        resp = await client.get("/admin-ui/renderer/support-matrix")
        assert resp.status_code == 200
        data = resp.json()
        assert float(data["version"].split(".")[0]) >= 8
        assert data["supported"]["delete"] is True
    asyncio.get_event_loop().run_until_complete(_run())


@pytest.mark.asyncio
async def test_support_matrix_endpoint_phase8(client):
    resp = await client.get("/admin-ui/renderer/support-matrix")
    assert resp.status_code == 200
    data = resp.json()
    assert float(data["version"].split(".")[0]) >= 8
    assert data["supported"]["delete"] is True
    assert data["supported"]["dangerous_actions"] is True


# ---------------------------------------------------------------------------
# confirm_delete page
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confirm_delete_page_renders(client):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/admin-ui/user/{fake_id}/delete")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_confirm_delete_has_danger_zone(client):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/admin-ui/user/{fake_id}/delete")
    assert b"danger-zone" in resp.content


@pytest.mark.asyncio
async def test_confirm_delete_has_warning_text(client):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/admin-ui/user/{fake_id}/delete")
    assert b"irreversible" in resp.content


@pytest.mark.asyncio
async def test_confirm_delete_has_confirm_button(client):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/admin-ui/user/{fake_id}/delete")
    assert b"confirm-delete-btn" in resp.content
    assert b"btn-danger" in resp.content


@pytest.mark.asyncio
async def test_confirm_delete_aria_describedby(client):
    """Danger button must reference the warning text for accessibility."""
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/admin-ui/user/{fake_id}/delete")
    assert b"aria-describedby" in resp.content
    assert b"delete-warning" in resp.content


@pytest.mark.asyncio
async def test_confirm_delete_no_protected_fields(client):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/admin-ui/user/{fake_id}/delete")
    assert b"hashed_password" not in resp.content
    assert b"SECRET_KEY" not in resp.content


# ---------------------------------------------------------------------------
# UIPreference schema — unit tests
# ---------------------------------------------------------------------------

def test_preference_defaults():
    p = UIPreference()
    assert p.density == "comfortable"
    assert p.visible_columns == {}
    assert p.sorting == {}
    assert p.navigation_favorites == []


def test_preference_round_trip():
    p = UIPreference(
        density="compact",
        visible_columns={"user": ["email", "is_active"]},
        sorting={"user": "-created_at"},
        navigation_favorites=["user", "tenant"],
    )
    dumped = p.model_dump()
    restored = UIPreference.model_validate(dumped)
    assert restored.density == "compact"
    assert restored.visible_columns["user"] == ["email", "is_active"]
    assert restored.navigation_favorites == ["user", "tenant"]


def test_preference_schema_has_no_security_fields():
    """Schema must not allow encoding permissions or superadmin overrides."""
    fields = UIPreference.model_fields
    for forbidden in ("is_superadmin", "permissions", "roles", "token", "password"):
        assert forbidden not in fields


def test_preference_store_isolation():
    clear_preferences()
    set_preferences("user-1", UIPreference(density="compact"))
    set_preferences("user-2", UIPreference(density="spacious"))
    assert get_preferences("user-1").density == "compact"
    assert get_preferences("user-2").density == "spacious"
    clear_preferences()


def test_preference_store_default_when_missing():
    clear_preferences()
    p = get_preferences("nobody")
    assert p == UIPreference()
    clear_preferences()


# ---------------------------------------------------------------------------
# Preference API endpoints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preferences_get_requires_auth(client):
    resp = await client.get("/api/v1/admin/preferences")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_preferences_put_requires_auth(client):
    resp = await client.put("/api/v1/admin/preferences", json={"density": "compact"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_preferences_get_returns_defaults(client, superadmin):
    clear_preferences()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/preferences",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["density"] == "comfortable"
    assert data["visible_columns"] == {}
    clear_preferences()


@pytest.mark.asyncio
async def test_preferences_put_updates(client, superadmin):
    clear_preferences()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.put(
        "/api/v1/admin/preferences",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "density": "compact",
            "visible_columns": {"user": ["email", "full_name"]},
            "sorting": {},
            "navigation_favorites": [],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["density"] == "compact"
    clear_preferences()


@pytest.mark.asyncio
async def test_preferences_persist_across_requests(client, superadmin):
    clear_preferences()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    await client.put(
        "/api/v1/admin/preferences",
        headers=headers,
        json={"density": "spacious", "visible_columns": {}, "sorting": {}, "navigation_favorites": []},
    )
    resp = await client.get("/api/v1/admin/preferences", headers=headers)
    assert resp.json()["density"] == "spacious"
    clear_preferences()


# ---------------------------------------------------------------------------
# Impersonation indicator in base template (regression + Phase 8 verification)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_base_template_has_impersonation_banner_class(client):
    """The impersonation banner is JS-injected by initNav() when ctx.is_impersonating is true.
    The CSS file must define the class; the HTML shell sets up the DOM structure for JS use."""
    resp = await client.get("/admin-ui/static/admin.css")
    assert b"impersonation-banner" in resp.content


@pytest.mark.asyncio
async def test_base_template_has_tenant_ctx_element(client):
    resp = await client.get("/admin-ui/dashboard")
    assert b"tenant-ctx" in resp.content


# ---------------------------------------------------------------------------
# Validation error containers in create/update forms
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_form_has_toast_container(client):
    resp = await client.get("/admin-ui/user/new")
    # inline alert replaced by toast — page must have the form and toast container
    assert b'id="record-form"' in resp.content
    assert b'toast-container' in resp.content


@pytest.mark.asyncio
async def test_update_form_has_toast_container(client):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/admin-ui/user/{fake_id}/edit")
    assert b'id="record-form"' in resp.content
    assert b'toast-container' in resp.content


# ---------------------------------------------------------------------------
# Regression — Phase 7 routes still work
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_page_still_works(client):
    resp = await client.get("/admin-ui/login")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_page_still_works(client):
    resp = await client.get("/admin-ui/user")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_detail_page_still_works(client):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/admin-ui/user/{fake_id}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_edit_page_still_works(client):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/admin-ui/user/{fake_id}/edit")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_still_works(client):
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_admin_still_works(client, superadmin):
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
