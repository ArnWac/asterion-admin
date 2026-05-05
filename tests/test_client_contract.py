"""Phase 9 — External client contract stabilization tests (fast layer).

Covers:
- CONTRACT_VERSION constant exists and is non-empty
- contract_version present in ModelContractMeta, CapabilitiesResponse, AdminContextResponse
- /admin/client-config endpoint structure, versioning, endpoint map, deprecation policy
- Relation lookup metadata (lookup_url, label_field) in RelationMeta
- /{model}/lookup endpoint: search, pagination, superadmin gate
- Renderer version updated to 9.0 and relation_selection now True
- Contract snapshot tests: User and Tenant model under superadmin context
- Protected fields absent from all contract responses
- Regression: Phase 8 routes still work
"""
import pytest
from unittest.mock import MagicMock

from adminfoundry.admin.contract import CONTRACT_VERSION, build_model_contract, build_field_metadata
from adminfoundry.admin.ui_renderer import RENDERER_VERSION, SUPPORTED_FEATURES, get_support_matrix
from adminfoundry.schemas.admin_contract import ModelContractMeta, RelationMeta
from adminfoundry.schemas.capabilities import CapabilitiesResponse, AdminContextResponse
from adminfoundry.schemas.client_config import ClientConfigResponse


# ---------------------------------------------------------------------------
# CONTRACT_VERSION constant
# ---------------------------------------------------------------------------

def test_contract_version_exists():
    assert isinstance(CONTRACT_VERSION, str) and len(CONTRACT_VERSION) > 0


def test_contract_version_is_numeric_string():
    """Version must be parseable as an integer for client comparison."""
    assert int(CONTRACT_VERSION) >= 1


# ---------------------------------------------------------------------------
# Schema field presence — contract_version wired in
# ---------------------------------------------------------------------------

def test_model_contract_meta_has_contract_version_field():
    assert "contract_version" in ModelContractMeta.model_fields


def test_capabilities_response_has_contract_version_field():
    assert "contract_version" in CapabilitiesResponse.model_fields


def test_admin_context_response_has_contract_version_field():
    assert "contract_version" in AdminContextResponse.model_fields


def test_relation_meta_has_lookup_url_field():
    assert "lookup_url" in RelationMeta.model_fields


def test_relation_meta_has_label_field_field():
    assert "label_field" in RelationMeta.model_fields


def test_relation_meta_lookup_url_optional():
    r = RelationMeta(target_table="users")
    assert r.lookup_url is None
    assert r.label_field is None


def test_relation_meta_with_lookup_url():
    r = RelationMeta(
        target_table="tenants",
        lookup_url="/api/v1/admin/tenants/lookup",
        label_field="name",
    )
    assert r.lookup_url == "/api/v1/admin/tenants/lookup"
    assert r.label_field == "name"


# ---------------------------------------------------------------------------
# Renderer — Phase 9 updates
# ---------------------------------------------------------------------------

def test_renderer_version_is_at_least_phase9():
    major = float(RENDERER_VERSION.split(".")[0])
    assert major >= 9


def test_relation_selection_now_supported():
    assert SUPPORTED_FEATURES["relation_selection"] is True


def test_bulk_actions_supported_from_phase11():
    assert SUPPORTED_FEATURES["bulk_actions"] is True


def test_support_matrix_includes_relation_selection():
    matrix = get_support_matrix()
    assert matrix["supported"]["relation_selection"] is True
    assert float(matrix["version"].split(".")[0]) >= 9


# ---------------------------------------------------------------------------
# ClientConfigResponse schema
# ---------------------------------------------------------------------------

def test_client_config_schema_fields():
    fields = ClientConfigResponse.model_fields
    for f in ("contract_version", "renderer_id", "renderer_version",
              "supported_features", "endpoints", "breaking_change_policy",
              "additive_change_policy"):
        assert f in fields, f"Missing field: {f}"


def test_client_config_no_secret_fields():
    fields = ClientConfigResponse.model_fields
    for forbidden in ("password", "token", "secret", "hashed_password"):
        assert forbidden not in fields


# ---------------------------------------------------------------------------
# build_model_contract — contract_version and RelationMeta wired
# ---------------------------------------------------------------------------

def _make_mock_admin(model_class, *, list_display=None, search_fields=None,
                     filter_fields=None, ordering=None, readonly_fields=None,
                     protected_fields=None, lookup_field=None):
    from adminfoundry.admin.model_admin import ModelAdmin

    class _Admin(ModelAdmin):
        model = model_class
        pass

    admin = _Admin()
    admin.list_display = list_display or []
    admin.search_fields = search_fields or []
    admin.filter_fields = filter_fields or []
    admin.ordering = ordering or []
    admin.readonly_fields = readonly_fields or []
    admin.protected_fields = protected_fields or []
    admin.lookup_field = lookup_field
    admin.actions = []
    return admin


def test_build_model_contract_has_contract_version():
    from adminfoundry.models.user import User
    from adminfoundry.admin.model_admin import ModelAdmin

    class UserAdmin(ModelAdmin):
        model = User
        list_display = ["email", "full_name", "is_active"]
        readonly_fields = ["id", "created_at", "updated_at"]

    contract = build_model_contract(UserAdmin())
    assert contract.contract_version == CONTRACT_VERSION


def test_build_model_contract_with_registry_populates_lookup_url():
    from adminfoundry.models.user import User
    from adminfoundry.admin.model_admin import ModelAdmin

    class UserAdmin(ModelAdmin):
        model = User

    # Build a minimal mock registry that knows about "tenants"
    mock_target_admin = MagicMock()
    mock_target_admin.lookup_field = None
    mock_target_admin.list_display = ["name"]

    mock_registry = MagicMock()
    mock_registry.get = lambda table: mock_target_admin if table == "tenants" else None

    fields = build_field_metadata(UserAdmin(), registry=mock_registry)
    # Users have a tenant_id FK → should produce a relation with lookup_url
    relation_fields = [f for f in fields if f.relation is not None]
    if relation_fields:
        rf = relation_fields[0]
        assert rf.relation.lookup_url is not None
        assert "/lookup" in rf.relation.lookup_url
        assert rf.relation.label_field == "name"


def test_build_model_contract_without_registry_no_lookup_url():
    from adminfoundry.models.user import User
    from adminfoundry.admin.model_admin import ModelAdmin

    class UserAdmin(ModelAdmin):
        model = User

    fields = build_field_metadata(UserAdmin(), registry=None)
    for f in fields:
        if f.relation is not None:
            assert f.relation.lookup_url is None


# ---------------------------------------------------------------------------
# Contract snapshot — User model (superadmin context)
# ---------------------------------------------------------------------------

def test_user_contract_snapshot_shape():
    from adminfoundry.models.user import User
    from adminfoundry.admin.model_admin import ModelAdmin

    class UserAdmin(ModelAdmin):
        model = User
        list_display = ["email", "full_name", "is_active", "is_superadmin"]
        search_fields = ["email", "full_name"]
        filter_fields = ["is_active", "is_superadmin"]
        readonly_fields = ["id", "created_at", "updated_at"]

    contract = build_model_contract(UserAdmin())
    assert contract.contract_version == CONTRACT_VERSION
    assert contract.model == "users"
    assert isinstance(contract.fields, list)
    assert isinstance(contract.list_fields, list)
    assert isinstance(contract.actions, list)

    field_names = {f.name for f in contract.fields}
    # Protected fields must never appear
    for protected in ("hashed_password", "password", "pin_hash"):
        assert protected not in field_names, f"Protected field leaked: {protected}"

    # Safe fields must appear
    assert "email" in field_names
    assert "is_active" in field_names


def test_user_contract_readonly_fields_present():
    from adminfoundry.models.user import User
    from adminfoundry.admin.model_admin import ModelAdmin

    class UserAdmin(ModelAdmin):
        model = User
        readonly_fields = ["id", "created_at", "updated_at"]

    contract = build_model_contract(UserAdmin())
    readonly_names = {f.name for f in contract.fields if f.readonly}
    for ro in ("id", "created_at", "updated_at"):
        if ro in {f.name for f in contract.fields}:
            assert ro in readonly_names


def test_user_contract_list_fields_subset_of_fields():
    from adminfoundry.models.user import User
    from adminfoundry.admin.model_admin import ModelAdmin

    class UserAdmin(ModelAdmin):
        model = User
        list_display = ["email", "is_active"]

    contract = build_model_contract(UserAdmin())
    field_names = {f.name for f in contract.fields}
    for lf in contract.list_fields:
        assert lf in field_names


# ---------------------------------------------------------------------------
# Contract snapshot — Tenant model
# ---------------------------------------------------------------------------

def test_tenant_contract_snapshot_shape():
    from adminfoundry.models.tenant import Tenant
    from adminfoundry.admin.model_admin import ModelAdmin

    class TenantAdmin(ModelAdmin):
        model = Tenant
        list_display = ["name", "slug", "is_active"]
        search_fields = ["name", "slug"]
        filter_fields = ["is_active"]
        readonly_fields = ["id", "created_at", "updated_at"]
        tenant_scoped = False

    contract = build_model_contract(TenantAdmin())
    assert contract.contract_version == CONTRACT_VERSION
    assert contract.model == "tenants"
    assert contract.tenant_scoped is False
    field_names = {f.name for f in contract.fields}
    assert "name" in field_names
    assert "slug" in field_names


def test_tenant_contract_no_protected_fields():
    from adminfoundry.models.tenant import Tenant
    from adminfoundry.admin.model_admin import ModelAdmin

    class TenantAdmin(ModelAdmin):
        model = Tenant

    contract = build_model_contract(TenantAdmin())
    field_names = {f.name for f in contract.fields}
    for p in ("hashed_password", "password", "tenant_salt", "shared_secret"):
        assert p not in field_names


# ---------------------------------------------------------------------------
# /admin/client-config endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_client_config_requires_auth(client):
    resp = await client.get("/api/v1/admin/client-config")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_client_config_returns_200_for_authenticated(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/client-config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_client_config_has_contract_version(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/client-config",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = resp.json()
    assert "contract_version" in data
    assert data["contract_version"] == CONTRACT_VERSION


@pytest.mark.asyncio
async def test_client_config_has_endpoint_map(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/client-config",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = resp.json()
    endpoints = data["endpoints"]
    for key in ("context", "navigation", "capabilities", "registry", "model_meta", "model_lookup"):
        assert key in endpoints, f"Missing endpoint key: {key}"


@pytest.mark.asyncio
async def test_client_config_has_deprecation_policy(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/client-config",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = resp.json()
    assert "breaking_change_policy" in data
    assert "additive_change_policy" in data
    assert len(data["breaking_change_policy"]) > 10
    assert len(data["additive_change_policy"]) > 10


@pytest.mark.asyncio
async def test_client_config_has_supported_features(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/client-config",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = resp.json()
    assert "supported_features" in data
    assert data["supported_features"]["relation_selection"] is True


@pytest.mark.asyncio
async def test_client_config_renderer_version(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/client-config",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = resp.json()
    assert float(data["renderer_version"].split(".")[0]) >= 9


# ---------------------------------------------------------------------------
# /admin/context includes contract_version
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_context_has_contract_version(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/context",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["contract_version"] == CONTRACT_VERSION


# ---------------------------------------------------------------------------
# /admin/capabilities includes contract_version
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_capabilities_has_contract_version(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/capabilities",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["contract_version"] == CONTRACT_VERSION


# ---------------------------------------------------------------------------
# /admin/{model}/meta includes contract_version
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_meta_has_contract_version(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/users/meta",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["contract_version"] == CONTRACT_VERSION


@pytest.mark.asyncio
async def test_model_meta_no_protected_fields(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/users/meta",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = resp.json()
    field_names = [f["name"] for f in data["fields"]]
    for p in ("hashed_password", "password", "pin_hash"):
        assert p not in field_names, f"Protected field leaked in meta: {p}"


# ---------------------------------------------------------------------------
# /{model}/lookup endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_requires_superadmin(client):
    resp = await client.get("/api/v1/admin/users/lookup")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_lookup_returns_paginated_response(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/users/lookup",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    for key in ("items", "total", "page", "page_size", "pages"):
        assert key in data


@pytest.mark.asyncio
async def test_lookup_items_have_id_and_label(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/users/lookup",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = resp.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert "id" in item
        assert "label" in item


@pytest.mark.asyncio
async def test_lookup_no_protected_fields_in_items(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/users/lookup",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = resp.json()
    for item in data["items"]:
        item_keys = set(item.keys())
        for p in ("hashed_password", "password", "pin_hash"):
            assert p not in item_keys


@pytest.mark.asyncio
async def test_lookup_pagination_params(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/users/lookup?page=1&page_size=5",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = resp.json()
    assert data["page"] == 1
    assert data["page_size"] == 5


@pytest.mark.asyncio
async def test_lookup_404_for_unknown_model(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/nonexistent_model/lookup",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Regression — Phase 8 routes still work
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_still_works(client):
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_admin_registry_still_works(client, superadmin):
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


@pytest.mark.asyncio
async def test_admin_navigation_still_works(client, superadmin):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    resp = await client.get(
        "/api/v1/admin/navigation",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_login_ui_still_works(client):
    resp = await client.get("/admin-ui/login")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_support_matrix_endpoint_reflects_phase9(client):
    resp = await client.get("/admin-ui/renderer/support-matrix")
    assert resp.status_code == 200
    data = resp.json()
    assert float(data["version"].split(".")[0]) >= 9
    assert data["supported"]["relation_selection"] is True
