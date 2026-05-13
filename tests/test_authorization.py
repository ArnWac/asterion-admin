"""Phase 10 — Fine-grained authorization and policy engine tests (fast layer).

Covers:
- Policy engine: field visibility/editability, record filter, record access, action policy
- Superadmin bypass and impersonation downgrade
- Effective capabilities reflect policy for current user
- CRUD endpoint enforcement: model access gate, record access, field edit
- /policy endpoint: field-level and model-level capabilities
- Protected fields unaffected by policy grants
- Schema: FieldMeta.policy_visible, FieldMeta.policy_editable
- Regression: Phase 9 routes still work
"""
import pytest
import pytest_asyncio
from unittest.mock import MagicMock
from sqlalchemy import select

from adminfoundry.authz.rules import FieldPolicy, RecordPolicy
from adminfoundry.authz.policy_engine import PolicyEngine, policy_engine, _roles_allow
from adminfoundry.schemas.admin_contract import FieldMeta, ActionMeta
from adminfoundry.schemas.policy import FieldPolicyMeta, ModelPolicyResponse
from adminfoundry.admin.contract import (
    CONTRACT_VERSION, build_model_contract, build_model_contract_for_user,
)
from adminfoundry.admin.capabilities import build_capabilities
from adminfoundry.admin.model_admin import ModelAdmin
from adminfoundry.admin.registry import admin_site
from adminfoundry.admin.schema_builder import schema_builder
from adminfoundry.models.role import Role, user_roles
from adminfoundry.models.user import User
from adminfoundry.auth import create_access_token, hash_password
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Helpers — mock users and model admins
# ---------------------------------------------------------------------------

def _make_superadmin():
    u = MagicMock()
    u.is_superadmin = True
    u.roles = []
    return u


def _make_user(roles: list[str] = None):
    u = MagicMock()
    u.is_superadmin = False
    role_objs = []
    for name in (roles or []):
        r = MagicMock()
        r.name = name
        role_objs.append(r)
    u.roles = role_objs
    return u


def _payload(impersonated_by: str = None) -> dict:
    p = {}
    if impersonated_by:
        p["impersonated_by"] = impersonated_by
    return p


def _make_admin(**kwargs) -> ModelAdmin:
    class TestAdmin(ModelAdmin):
        model = Role
        list_display = ["name"]
        readonly_fields = ["id", "created_at", "updated_at"]

    for k, v in kwargs.items():
        setattr(TestAdmin, k, v)
    return TestAdmin()


# ---------------------------------------------------------------------------
# _roles_allow helper
# ---------------------------------------------------------------------------

def test_roles_allow_unrestricted():
    assert _roles_allow(None, _make_user()) is True


def test_roles_allow_empty_list_denies():
    assert _roles_allow([], _make_user(["manager"])) is False


def test_roles_allow_matching_role():
    assert _roles_allow(["manager"], _make_user(["manager"])) is True


def test_roles_allow_missing_role():
    assert _roles_allow(["admin"], _make_user(["manager"])) is False


# ---------------------------------------------------------------------------
# Policy engine — superadmin bypass
# ---------------------------------------------------------------------------

def test_privileged_superadmin_non_impersonated():
    pe = PolicyEngine()
    assert pe._privileged(_make_superadmin(), {}) is True


def test_privileged_superadmin_impersonated():
    pe = PolicyEngine()
    assert pe._privileged(_make_superadmin(), {"impersonated_by": "someone"}) is False


def test_privileged_non_superadmin():
    pe = PolicyEngine()
    assert pe._privileged(_make_user(), {}) is False


def test_evaluate_field_superadmin_full_access():
    pe = PolicyEngine()
    admin = _make_admin(field_policies={"name": {"view_roles": [], "edit_roles": []}})
    fp = pe.evaluate_field(_make_superadmin(), admin, "name", {})
    assert fp.can_view is True
    assert fp.can_edit is True


def test_evaluate_field_impersonated_superadmin_downgraded():
    pe = PolicyEngine()
    admin = _make_admin(field_policies={"name": {"view_roles": [], "edit_roles": []}})
    fp = pe.evaluate_field(_make_superadmin(), admin, "name", {"impersonated_by": "x"})
    assert fp.can_view is False
    assert fp.can_edit is False


# ---------------------------------------------------------------------------
# Policy engine — field policies
# ---------------------------------------------------------------------------

def test_field_policy_no_restriction():
    pe = PolicyEngine()
    admin = _make_admin()
    fp = pe.evaluate_field(_make_user(["manager"]), admin, "name", {})
    assert fp.can_view is True and fp.can_edit is True


def test_field_policy_view_only_edit_denied():
    """edit_roles=[] → can view but cannot edit (view_roles unrestricted)."""
    pe = PolicyEngine()
    admin = _make_admin(field_policies={"name": {"view_roles": None, "edit_roles": []}})
    fp = pe.evaluate_field(_make_user(["manager"]), admin, "name", {})
    assert fp.can_view is True
    assert fp.can_edit is False


def test_field_policy_hidden_view_denied():
    """view_roles=[] → cannot view or edit."""
    pe = PolicyEngine()
    admin = _make_admin(field_policies={"name": {"view_roles": [], "edit_roles": None}})
    fp = pe.evaluate_field(_make_user(["manager"]), admin, "name", {})
    assert fp.can_view is False
    assert fp.can_edit is False


def test_field_policy_view_requires_role_has_role():
    pe = PolicyEngine()
    admin = _make_admin(field_policies={"name": {"view_roles": ["manager"]}})
    fp = pe.evaluate_field(_make_user(["manager"]), admin, "name", {})
    assert fp.can_view is True


def test_field_policy_view_requires_role_missing_role():
    pe = PolicyEngine()
    admin = _make_admin(field_policies={"name": {"view_roles": ["manager"]}})
    fp = pe.evaluate_field(_make_user([]), admin, "name", {})
    assert fp.can_view is False
    assert fp.can_edit is False  # no view → no edit


# ---------------------------------------------------------------------------
# Policy engine — record filter
# ---------------------------------------------------------------------------

def test_record_filter_superadmin_returns_none():
    pe = PolicyEngine()
    admin = _make_admin(record_filter=lambda user: Role.name == "whatever")
    assert pe.get_record_filter(_make_superadmin(), admin, {}) is None


def test_record_filter_non_superadmin_calls_callable():
    pe = PolicyEngine()
    sentinel = object()
    admin = _make_admin(record_filter=lambda user: sentinel)
    result = pe.get_record_filter(_make_user(), admin, {})
    assert result is sentinel


def test_record_filter_none_configured():
    pe = PolicyEngine()
    admin = _make_admin()
    assert pe.get_record_filter(_make_user(), admin, {}) is None


# ---------------------------------------------------------------------------
# Policy engine — record access
# ---------------------------------------------------------------------------

def test_record_access_superadmin_full():
    pe = PolicyEngine()
    record = MagicMock()
    admin = _make_admin(record_access=lambda user, rec: False)  # would deny
    rp = pe.check_record_access(_make_superadmin(), admin, record, {})
    assert rp.can_read and rp.can_update and rp.can_delete


def test_record_access_allowed():
    pe = PolicyEngine()
    record = MagicMock()
    admin = _make_admin(record_access=lambda user, rec: True)
    rp = pe.check_record_access(_make_user(), admin, record, {})
    assert rp.can_read and rp.can_update and rp.can_delete


def test_record_access_denied():
    pe = PolicyEngine()
    record = MagicMock()
    admin = _make_admin(record_access=lambda user, rec: False)
    rp = pe.check_record_access(_make_user(), admin, record, {})
    assert not rp.can_read
    assert not rp.can_update
    assert not rp.can_delete


def test_record_access_no_callable_allows_all():
    pe = PolicyEngine()
    record = MagicMock()
    admin = _make_admin()
    rp = pe.check_record_access(_make_user(), admin, record, {})
    assert rp.can_read and rp.can_update and rp.can_delete


# ---------------------------------------------------------------------------
# Policy engine — action policy
# ---------------------------------------------------------------------------

def test_action_policy_no_restriction():
    pe = PolicyEngine()
    admin = _make_admin()
    assert pe.can_perform_action(_make_user(), admin, "deactivate", {}) is True


def test_action_policy_superadmin_bypass():
    pe = PolicyEngine()
    admin = _make_admin(action_policies={"deactivate": {"roles": []}})
    assert pe.can_perform_action(_make_superadmin(), admin, "deactivate", {}) is True


def test_action_policy_allowed_by_role():
    pe = PolicyEngine()
    admin = _make_admin(action_policies={"deactivate": {"roles": ["manager"]}})
    assert pe.can_perform_action(_make_user(["manager"]), admin, "deactivate", {}) is True


def test_action_policy_denied_missing_role():
    pe = PolicyEngine()
    admin = _make_admin(action_policies={"deactivate": {"roles": ["manager"]}})
    assert pe.can_perform_action(_make_user([]), admin, "deactivate", {}) is False


# ---------------------------------------------------------------------------
# Policy engine — effective model capabilities
# ---------------------------------------------------------------------------

def test_effective_caps_superadmin_all_true():
    pe = PolicyEngine()
    admin = _make_admin(admin_only=True)
    caps = pe.effective_model_caps(_make_superadmin(), admin, {})
    assert all(caps[k] for k in ["can_list", "can_create", "can_read", "can_update", "can_delete"])


def test_effective_caps_admin_only_non_superadmin_all_false():
    pe = PolicyEngine()
    admin = _make_admin(admin_only=True)
    caps = pe.effective_model_caps(_make_user(["manager"]), admin, {})
    assert not any(caps[k] for k in ["can_list", "can_create", "can_read", "can_update", "can_delete"])


def test_effective_caps_policy_gated_has_access():
    pe = PolicyEngine()
    admin = _make_admin(admin_only=False, access_roles=["manager"])
    caps = pe.effective_model_caps(_make_user(["manager"]), admin, {})
    assert caps["can_list"] and caps["can_read"]


def test_effective_caps_policy_gated_missing_role():
    pe = PolicyEngine()
    admin = _make_admin(admin_only=False, access_roles=["manager"])
    caps = pe.effective_model_caps(_make_user([]), admin, {})
    assert not any(caps[k] for k in ["can_list", "can_read"])



def test_effective_caps_action_policy_gates_update():
    pe = PolicyEngine()
    admin = _make_admin(
        admin_only=False,
        access_roles=["manager"],
        action_policies={"update": {"roles": []}},  # only superadmin can update
    )
    caps = pe.effective_model_caps(_make_user(["manager"]), admin, {})
    assert caps["can_list"] is True
    assert caps["can_update"] is False


# ---------------------------------------------------------------------------
# Schema — FieldMeta and ActionMeta phase 10 fields
# ---------------------------------------------------------------------------

def test_field_meta_has_policy_visible():
    assert "policy_visible" in FieldMeta.model_fields


def test_field_meta_has_policy_editable():
    assert "policy_editable" in FieldMeta.model_fields


def test_field_meta_policy_defaults_to_true():
    f = FieldMeta(
        name="email", label="Email", field_type="string",
        required=True, nullable=False, has_default=False, readonly=False,
        in_list=True, searchable=True, filterable=False, sortable=True, widget="text",
    )
    assert f.policy_visible is True
    assert f.policy_editable is True


def test_model_policy_response_schema():
    r = ModelPolicyResponse(
        model="users",
        contract_version="1",
        can_list=True, can_create=False, can_read=True, can_update=False, can_delete=False,
        field_policies=[FieldPolicyMeta(field="email", can_view=True, can_edit=False)],
    )
    assert r.model == "users"
    assert r.field_policies[0].can_edit is False


def test_field_policy_meta_schema():
    fp = FieldPolicyMeta(field="name", can_view=True, can_edit=False)
    assert fp.field == "name"
    assert fp.can_view is True
    assert fp.can_edit is False


# ---------------------------------------------------------------------------
# Contract — build_model_contract_for_user
# ---------------------------------------------------------------------------

def test_build_model_contract_for_user_superadmin_all_visible():
    from examples.default.admin_config import UserAdmin
    admin = admin_site.get("users")
    user = _make_superadmin()
    contract = build_model_contract_for_user(admin, user, {})
    for f in contract.fields:
        assert f.policy_visible is True
        assert f.policy_editable is True


def test_build_model_contract_for_user_field_restricted():
    class RestrictedAdmin(ModelAdmin):
        model = Role
        list_display = ["name"]
        readonly_fields = ["id", "created_at", "updated_at"]
        field_policies = {"name": {"view_roles": None, "edit_roles": []}}

    admin_inst = RestrictedAdmin()
    user = _make_user(["manager"])
    contract = build_model_contract_for_user(admin_inst, user, {}, registry=None)
    name_field = next(f for f in contract.fields if f.name == "name")
    assert name_field.policy_visible is True
    assert name_field.policy_editable is False


def test_build_model_contract_protected_fields_absent():
    """Protected fields must be absent from the contract regardless of policy."""
    admin = admin_site.get("users")
    user = _make_superadmin()
    contract = build_model_contract_for_user(admin, user, {})
    field_names = [f.name for f in contract.fields]
    assert "hashed_password" not in field_names
    assert "password" not in field_names


# ---------------------------------------------------------------------------
# HTTP tests — capabilities, /policy endpoint, CRUD enforcement
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def manager_user(db: AsyncSession) -> User:
    role = Role(name="manager")
    db.add(role)
    user = User(
        email="manager@example.com",
        hashed_password=hash_password("password123"),
        full_name="Manager User",
        is_active=True,
        is_superadmin=False,
    )
    db.add(user)
    await db.flush()
    await db.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))
    await db.commit()
    result = await db.execute(select(User).where(User.id == user.id))
    return result.scalar_one()


@pytest_asyncio.fixture
async def regular_user(db: AsyncSession) -> User:
    user = User(
        email="regular@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_superadmin=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest.fixture
def policy_role_admin():
    """Register a policy-gated admin over the 'roles' table; restore after test."""
    old_admin = admin_site.get("roles")

    class PolicyRoleAdmin(ModelAdmin):
        model = Role
        admin_only = False
        access_roles = ["manager"]
        field_policies = {
            "name": {"view_roles": None, "edit_roles": []}  # view OK, edit superadmin-only
        }
        record_access = staticmethod(lambda user, record: record.name.startswith("public_"))
        record_filter = staticmethod(lambda user: Role.name.like("public_%"))
        list_display = ["name", "id"]
        search_fields = ["name"]
        readonly_fields = ["id", "created_at", "updated_at"]

    admin_site.register(PolicyRoleAdmin())
    schema_builder.invalidate("roles")
    yield
    if old_admin:
        admin_site.register(old_admin)
    else:
        del admin_site._registry["roles"]
    schema_builder.invalidate("roles")


def _auth(user: User) -> dict:
    token = create_access_token(str(user.id))
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_capabilities_superadmin_all_true(client, superadmin):
    resp = await client.get("/api/v1/admin/capabilities", headers=_auth(superadmin))
    assert resp.status_code == 200
    data = resp.json()
    for m in data["models"]:
        assert m["can_list"] and m["can_read"] and m["can_update"]


@pytest.mark.asyncio
async def test_capabilities_non_superadmin_all_false(client, regular_user):
    """Non-superadmin on all admin_only=True models gets all False."""
    resp = await client.get("/api/v1/admin/capabilities", headers=_auth(regular_user))
    assert resp.status_code == 200
    data = resp.json()
    for m in data["models"]:
        assert m["can_list"] is False
        assert m["can_create"] is False


@pytest.mark.asyncio
async def test_capabilities_policy_gated_model(client, manager_user, policy_role_admin):
    """Manager user gets can_list=True for policy-gated model."""
    resp = await client.get("/api/v1/admin/capabilities", headers=_auth(manager_user))
    assert resp.status_code == 200
    data = resp.json()
    roles_caps = next(m for m in data["models"] if m["model"] == "roles")
    assert roles_caps["can_list"] is True
    assert roles_caps["can_read"] is True


@pytest.mark.asyncio
async def test_crud_list_non_superadmin_denied_admin_only(client, regular_user):
    """Non-superadmin cannot list admin_only models."""
    resp = await client.get("/api/v1/admin/users", headers=_auth(regular_user))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_crud_list_record_filter_applied(client, manager_user, policy_role_admin, db):
    """Manager user only sees public_ records due to record_filter."""
    r1 = Role(name="public_alpha")
    r2 = Role(name="private_beta")
    db.add(r1)
    db.add(r2)
    await db.commit()

    resp = await client.get("/api/v1/admin/roles", headers=_auth(manager_user))
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()["items"]]
    assert "public_alpha" in names
    assert "private_beta" not in names


@pytest.mark.asyncio
async def test_crud_get_record_access_denied(client, manager_user, policy_role_admin, db):
    """Manager user gets 403 on a record that record_access denies."""
    role = Role(name="private_secret")
    db.add(role)
    await db.commit()

    resp = await client.get(f"/api/v1/admin/roles/{role.id}", headers=_auth(manager_user))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_crud_get_record_access_allowed(client, manager_user, policy_role_admin, db):
    """Manager user can access a public_ record."""
    role = Role(name="public_open")
    db.add(role)
    await db.commit()

    resp = await client.get(f"/api/v1/admin/roles/{role.id}", headers=_auth(manager_user))
    assert resp.status_code == 200
    assert resp.json()["name"] == "public_open"


@pytest.mark.asyncio
async def test_crud_update_field_policy_denied(client, manager_user, policy_role_admin, db):
    """Manager user cannot update 'name' field — edit_roles=[]."""
    role = Role(name="public_editable")
    db.add(role)
    await db.commit()

    resp = await client.patch(
        f"/api/v1/admin/roles/{role.id}",
        json={"name": "new_name"},
        headers=_auth(manager_user),
    )
    assert resp.status_code == 403
    assert "not editable" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_crud_update_record_access_denied(client, manager_user, policy_role_admin, db):
    """Manager user gets 403 updating a private_ record."""
    role = Role(name="private_locked")
    db.add(role)
    await db.commit()

    resp = await client.patch(
        f"/api/v1/admin/roles/{role.id}",
        json={"name": "private_locked2"},
        headers=_auth(manager_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_crud_delete_record_access_denied(client, manager_user, policy_role_admin, db):
    """Manager user gets 403 deleting a private_ record."""
    role = Role(name="private_del")
    db.add(role)
    await db.commit()

    resp = await client.delete(f"/api/v1/admin/roles/{role.id}", headers=_auth(manager_user))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_policy_endpoint_superadmin(client, superadmin):
    resp = await client.get("/api/v1/admin/users/policy", headers=_auth(superadmin))
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "users"
    assert data["can_list"] is True
    for fp in data["field_policies"]:
        assert fp["can_view"] is True
        assert fp["can_edit"] is True


@pytest.mark.asyncio
async def test_policy_endpoint_non_superadmin_admin_only(client, regular_user):
    """Non-superadmin on admin_only model sees all-False caps via /policy."""
    resp = await client.get("/api/v1/admin/users/policy", headers=_auth(regular_user))
    assert resp.status_code == 200
    data = resp.json()
    assert data["can_list"] is False


@pytest.mark.asyncio
async def test_policy_endpoint_policy_gated_field(client, manager_user, policy_role_admin):
    resp = await client.get("/api/v1/admin/roles/policy", headers=_auth(manager_user))
    assert resp.status_code == 200
    data = resp.json()
    name_policy = next(fp for fp in data["field_policies"] if fp["field"] == "name")
    assert name_policy["can_view"] is True
    assert name_policy["can_edit"] is False


@pytest.mark.asyncio
async def test_protected_fields_absent_from_policy_endpoint(client, superadmin):
    resp = await client.get("/api/v1/admin/users/policy", headers=_auth(superadmin))
    assert resp.status_code == 200
    field_names = [fp["field"] for fp in resp.json()["field_policies"]]
    assert "hashed_password" not in field_names


# ---------------------------------------------------------------------------
# Regression — superadmin CRUD still works
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_superadmin_can_list_users(client, superadmin):
    resp = await client.get("/api/v1/admin/users", headers=_auth(superadmin))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_superadmin_list_returns_all_records(client, superadmin, policy_role_admin, db):
    """Superadmin bypasses record_filter and sees all records."""
    r1 = Role(name="public_x")
    r2 = Role(name="private_y")
    db.add(r1)
    db.add(r2)
    await db.commit()

    resp = await client.get("/api/v1/admin/roles", headers=_auth(superadmin))
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()["items"]]
    assert "public_x" in names
    assert "private_y" in names


@pytest.mark.asyncio
async def test_superadmin_can_update_restricted_field(client, superadmin, policy_role_admin, db):
    """Superadmin can update a field that has edit_roles=[]."""
    role = Role(name="public_sup_edit")
    db.add(role)
    await db.commit()

    resp = await client.patch(
        f"/api/v1/admin/roles/{role.id}",
        json={"name": "public_sup_edit_new"},
        headers=_auth(superadmin),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "public_sup_edit_new"
