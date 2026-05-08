"""
Phase 6 — Admin contract and capability model tests.
Covers: contract generation, field metadata, protected-field filtering,
capabilities, navigation, admin context, relation metadata, action metadata,
contract snapshots for two representative models, and regressions.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.models.user import User
from adminfoundry.auth import create_access_token, create_impersonation_token, hash_password
from adminfoundry.admin import admin_site
from adminfoundry.admin.contract import build_field_metadata, build_model_contract
from adminfoundry.admin.capabilities import build_capabilities, build_admin_context
from adminfoundry.admin.navigation import build_navigation


def auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


# ---------------------------------------------------------------------------
# Unit: contract generation
# ---------------------------------------------------------------------------

def test_field_metadata_excludes_protected_fields():
    """hashed_password and other globally protected fields must not appear."""
    user_admin = admin_site.get("users")
    fields = build_field_metadata(user_admin)
    names = [f.name for f in fields]
    for protected in ["hashed_password", "password", "pin_hash"]:
        assert protected not in names, f"Protected field '{protected}' leaked into field metadata"


def test_field_metadata_includes_expected_user_fields():
    user_admin = admin_site.get("users")
    fields = build_field_metadata(user_admin)
    names = [f.name for f in fields]
    assert "email" in names
    assert "is_active" in names
    assert "is_superadmin" in names


def test_field_metadata_readonly_flags():
    user_admin = admin_site.get("users")
    fields = build_field_metadata(user_admin)
    by_name = {f.name: f for f in fields}
    assert by_name["id"].readonly is True
    assert by_name["created_at"].readonly is True
    assert by_name["email"].readonly is False


def test_field_metadata_searchable_filterable_flags():
    user_admin = admin_site.get("users")
    fields = build_field_metadata(user_admin)
    by_name = {f.name: f for f in fields}
    assert by_name["email"].searchable is True
    assert by_name["is_active"].filterable is True
    assert by_name["id"].searchable is False
    assert by_name["id"].filterable is False


def test_field_metadata_in_list_flag():
    user_admin = admin_site.get("users")
    fields = build_field_metadata(user_admin)
    by_name = {f.name: f for f in fields}
    assert by_name["email"].in_list is True
    assert by_name["is_active"].in_list is True


def test_field_metadata_widget_types():
    user_admin = admin_site.get("users")
    fields = build_field_metadata(user_admin)
    by_name = {f.name: f for f in fields}
    assert by_name["email"].widget == "text"
    assert by_name["is_active"].widget == "checkbox"
    assert by_name["id"].widget == "uuid-display"


def test_field_metadata_relation_for_tenant_id():
    """tenant_id FK should produce a relation meta entry when protected_fields excludes it."""
    from adminfoundry.admin.model_admin import ModelAdmin
    from adminfoundry.models.user import User

    class _MultiTenantUserAdmin(ModelAdmin):
        model = User
        list_display = ["email"]
        readonly_fields = ["id", "created_at", "updated_at"]
        protected_fields = []  # simulate MULTI_TENANT=True

    fields = build_field_metadata(_MultiTenantUserAdmin())
    by_name = {f.name: f for f in fields}
    tenant_id_field = by_name.get("tenant_id")
    assert tenant_id_field is not None
    assert tenant_id_field.relation is not None
    assert tenant_id_field.relation.target_table == "tenants"
    assert tenant_id_field.widget == "select-relation"


# ---------------------------------------------------------------------------
# Contract snapshots — UserAdmin (complex) and RoleAdmin (simple)
# ---------------------------------------------------------------------------

def test_contract_snapshot_user_admin():
    """Snapshot test: UserAdmin contract shape."""
    user_admin = admin_site.get("users")
    contract = build_model_contract(user_admin)

    assert contract.model == "users"
    assert contract.label == "User"
    assert contract.label_plural == "Users"
    assert contract.description == "Registered application users"
    assert contract.tenant_scoped is False
    assert "email" in contract.search_fields
    assert "is_active" in contract.filter_fields
    assert "id" in contract.readonly_fields

    # Actions snapshot
    assert len(contract.actions) == 1
    action = contract.actions[0]
    assert action.name == "deactivate"
    assert action.danger is True
    assert action.confirm is True
    assert action.bulk is True

    # Protected fields absent
    field_names = [f.name for f in contract.fields]
    assert "hashed_password" not in field_names


def test_contract_snapshot_role_admin():
    """Snapshot test: RoleAdmin contract shape — simpler model, no protected fields."""
    role_admin = admin_site.get("roles")
    contract = build_model_contract(role_admin)

    assert contract.model == "roles"
    assert contract.label == "Permission Group"
    assert contract.label_plural == "Permissions"
    assert contract.description == "Permission groups assignable to users — CRUD capabilities configured below"
    assert contract.tenant_scoped is False
    assert contract.actions == []

    field_names = [f.name for f in contract.fields]
    assert "name" in field_names
    assert "id" in field_names

    by_name = {f.name: f for f in contract.fields}
    assert by_name["name"].field_type == "string"
    assert by_name["name"].widget == "text"
    assert by_name["id"].readonly is True


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

def test_superadmin_has_full_capabilities():
    from unittest.mock import MagicMock
    user = MagicMock()
    user.is_superadmin = True
    payload = {}
    caps = build_capabilities(user, payload, admin_site)

    assert caps.is_superadmin is True
    assert caps.is_impersonating is False
    assert caps.impersonated_by is None
    for model_cap in caps.models:
        assert model_cap.can_list is True
        assert model_cap.can_create is True
        assert model_cap.can_delete is True


def test_impersonation_token_has_no_capabilities():
    from unittest.mock import MagicMock
    user = MagicMock()
    user.is_superadmin = True
    payload = {"impersonated_by": "some-superadmin-id"}
    caps = build_capabilities(user, payload, admin_site)

    assert caps.is_impersonating is True
    assert caps.impersonated_by == "some-superadmin-id"
    for model_cap in caps.models:
        assert model_cap.can_list is False


def test_non_superadmin_has_no_capabilities():
    from unittest.mock import MagicMock
    user = MagicMock()
    user.is_superadmin = False
    payload = {}
    caps = build_capabilities(user, payload, admin_site)

    assert caps.is_superadmin is False
    for model_cap in caps.models:
        assert model_cap.can_list is False


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def test_superadmin_sees_all_models_in_navigation():
    from unittest.mock import MagicMock
    user = MagicMock()
    user.is_superadmin = True
    payload = {}
    nav = build_navigation(user, payload, admin_site)

    model_names = [item.model for item in nav.items]
    assert "users" in model_names
    assert "roles" in model_names
    # tenants only registered when MULTI_TENANT=True; skip assertion in default config


def test_impersonation_token_sees_empty_navigation():
    from unittest.mock import MagicMock
    user = MagicMock()
    user.is_superadmin = True
    payload = {"impersonated_by": "some-id"}
    nav = build_navigation(user, payload, admin_site)
    assert nav.items == []


def test_non_superadmin_sees_empty_navigation():
    from unittest.mock import MagicMock
    user = MagicMock()
    user.is_superadmin = False
    payload = {}
    nav = build_navigation(user, payload, admin_site)
    assert nav.items == []


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_context_endpoint(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/admin/context", headers=auth(superadmin))
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == superadmin.email
    assert data["is_superadmin"] is True
    assert data["is_impersonating"] is False
    assert "hashed_password" not in str(data)
    assert "password" not in str(data)


@pytest.mark.asyncio
async def test_admin_context_shows_impersonation_state(client: AsyncClient, superadmin: User):
    imp_token, _ = create_impersonation_token(str(superadmin.id), str(superadmin.id))
    resp = await client.get(
        "/api/v1/admin/context",
        headers={"Authorization": f"Bearer {imp_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_impersonating"] is True
    assert data["impersonated_by"] is not None


@pytest.mark.asyncio
async def test_admin_navigation_endpoint(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/admin/navigation", headers=auth(superadmin))
    assert resp.status_code == 200
    items = resp.json()["items"]
    model_names = [i["model"] for i in items]
    assert "users" in model_names
    assert "roles" in model_names
    # Each item has required fields
    for item in items:
        assert "label" in item
        assert "label_plural" in item
        assert "url" in item
        assert "tenant_scoped" in item


@pytest.mark.asyncio
async def test_admin_capabilities_endpoint(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/admin/capabilities", headers=auth(superadmin))
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_superadmin"] is True
    assert data["is_impersonating"] is False
    for model_cap in data["models"]:
        assert model_cap["can_list"] is True


@pytest.mark.asyncio
async def test_admin_capabilities_impersonation(client: AsyncClient, superadmin: User):
    """Impersonation token → all model capabilities are False."""
    imp_token, _ = create_impersonation_token(str(superadmin.id), str(superadmin.id))
    resp = await client.get(
        "/api/v1/admin/capabilities",
        headers={"Authorization": f"Bearer {imp_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_impersonating"] is True
    for model_cap in data["models"]:
        assert model_cap["can_list"] is False


@pytest.mark.asyncio
async def test_model_meta_endpoint_users(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/admin/users/meta", headers=auth(superadmin))
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "users"
    assert data["label"] == "User"
    assert data["label_plural"] == "Users"
    assert "fields" in data
    assert "actions" in data
    # Protected fields absent
    field_names = [f["name"] for f in data["fields"]]
    assert "hashed_password" not in field_names
    assert "email" in field_names


@pytest.mark.asyncio
async def test_model_meta_endpoint_roles(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/admin/roles/meta", headers=auth(superadmin))
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "roles"
    field_names = [f["name"] for f in data["fields"]]
    assert "name" in field_names


@pytest.mark.asyncio
async def test_model_meta_not_found(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/admin/nonexistent/meta", headers=auth(superadmin))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_model_meta_requires_superadmin(client: AsyncClient, db: AsyncSession):
    user = User(
        email="plain4@x.com",
        hashed_password=hash_password("pw"),
        is_active=True,
        is_superadmin=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    resp = await client.get("/api/v1/admin/users/meta", headers=auth(user))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_contract_no_protected_fields_in_any_endpoint(client: AsyncClient, superadmin: User):
    """Meta endpoints must never expose protected field names."""
    for model in ["users", "roles", "tenants"]:
        resp = await client.get(f"/api/v1/admin/{model}/meta", headers=auth(superadmin))
        data = resp.json()
        field_names = {f["name"] for f in data.get("fields", [])}
        for protected in ["hashed_password", "password", "pin_hash"]:
            assert protected not in field_names, f"'{protected}' leaked in /admin/{model}/meta"


# ---------------------------------------------------------------------------
# Regression: Phase 0–5 behavior unchanged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_crud_still_works(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/admin", headers=auth(superadmin))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_list_users_still_works(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/admin/users", headers=auth(superadmin))
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.asyncio
async def test_login_still_works(client: AsyncClient, superadmin: User):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_logout_still_works(client: AsyncClient, superadmin: User):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204
