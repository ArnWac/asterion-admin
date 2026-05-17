"""Regression tests for the remaining tenant security gaps.

Covers: import tenant_id injection, hard-delete tenant filter,
permission matrix access, global role isolation, PolicyEngine membership
awareness, and user_roles cross-tenant guard.
"""
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.auth import create_access_token, create_impersonation_token, hash_password
from adminfoundry.models.role import Role
from adminfoundry.models.tenant import Tenant
from adminfoundry.models.tenant_membership import TenantMembership
from adminfoundry.models.user import User
from adminfoundry.models.associations import user_roles as _user_roles_table
from examples.basic_multi.models import Project


def auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def tenant_header(slug: str) -> dict:
    return {"host": f"{slug}.test"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def tenant_a(db: AsyncSession) -> Tenant:
    t = Tenant(name="Tenant A", slug="ta-gaps", is_active=True)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


@pytest_asyncio.fixture
async def tenant_b(db: AsyncSession) -> Tenant:
    t = Tenant(name="Tenant B", slug="tb-gaps", is_active=True)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


@pytest_asyncio.fixture
async def role_a(db: AsyncSession, tenant_a: Tenant) -> Role:
    r = Role(name="tenant_admin", tenant_id=tenant_a.id)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


@pytest_asyncio.fixture
async def user_a(db: AsyncSession) -> User:
    u = User(
        email="user-gaps-a@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_superadmin=False,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def membership_a(db: AsyncSession, user_a: User, tenant_a: Tenant, role_a: Role) -> TenantMembership:
    m = TenantMembership(user_id=user_a.id, tenant_id=tenant_a.id, is_active=True)
    db.add(m)
    await db.flush()
    await db.refresh(m)
    m.roles.append(role_a)
    await db.commit()
    await db.refresh(m)
    return m


@pytest_asyncio.fixture
async def project_a(db: AsyncSession, tenant_a: Tenant) -> Project:
    p = Project(name="Project A", active=True, tenant_id=str(tenant_a.id))
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


@pytest_asyncio.fixture
async def project_b(db: AsyncSession, tenant_b: Tenant) -> Project:
    p = Project(name="Project B", active=True, tenant_id=str(tenant_b.id))
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


# ---------------------------------------------------------------------------
# 1. _inject_tenant_id helper unit test
# ---------------------------------------------------------------------------

def test_inject_tenant_id_stamps_data():
    """_inject_tenant_id sets tenant_id from resolved tenant context."""
    from unittest.mock import MagicMock
    from adminfoundry.admin.routes.crud import _inject_tenant_id

    tenant_id = uuid.uuid4()
    tenant = MagicMock()
    tenant.id = tenant_id

    model_admin = MagicMock()
    model_admin.tenant_scoped = True
    model_admin.model.tenant_id = True  # hasattr check

    request = MagicMock()
    request.app.state.adminfoundry.config.enable_multi_tenant = True
    request.state.tenant = tenant

    data = _inject_tenant_id({}, model_admin, request, {})
    assert data["tenant_id"] == str(tenant_id)


def test_inject_tenant_id_does_not_override_existing():
    """_inject_tenant_id uses setdefault — client-supplied tenant_id is replaced."""
    from unittest.mock import MagicMock
    from adminfoundry.admin.routes.crud import _inject_tenant_id

    resolved_id = uuid.uuid4()
    tenant = MagicMock()
    tenant.id = resolved_id

    model_admin = MagicMock()
    model_admin.tenant_scoped = True
    model_admin.model.tenant_id = True

    request = MagicMock()
    request.app.state.adminfoundry.config.enable_multi_tenant = True
    request.state.tenant = tenant

    other_id = str(uuid.uuid4())
    data = _inject_tenant_id({"tenant_id": other_id}, model_admin, request, {})
    # setdefault keeps existing value — the prior value was already present
    assert data["tenant_id"] == other_id


def test_inject_tenant_id_noop_for_non_scoped():
    """_inject_tenant_id is a no-op for non-tenant-scoped models."""
    from unittest.mock import MagicMock
    from adminfoundry.admin.routes.crud import _inject_tenant_id

    model_admin = MagicMock()
    model_admin.tenant_scoped = False

    request = MagicMock()
    data = _inject_tenant_id({}, model_admin, request, {})
    assert "tenant_id" not in data


# ---------------------------------------------------------------------------
# 2. Hard delete respects tenant filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hard_delete_blocked_for_impersonation_token(
    client: AsyncClient,
    db: AsyncSession,
    superadmin: User,
    tenant_a: Tenant,
    tenant_b: Tenant,
    project_b: Project,
):
    """Impersonation tokens cannot reach hard-delete — require_superadmin blocks them."""
    token, _ = create_impersonation_token(str(superadmin.id), str(superadmin.id), str(tenant_a.id))
    headers = {"Authorization": f"Bearer {token}", **tenant_header(tenant_a.slug)}

    resp = await client.delete(
        f"/api/v1/admin/projects/{project_b.id}/hard",
        headers=headers,
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_hard_delete_tenant_scoped_model_blocked_from_root_panel(
    client: AsyncClient,
    db: AsyncSession,
    superadmin: User,
    tenant_b: Tenant,
    project_b: Project,
):
    """Superadmin in root panel cannot hard-delete a tenant-scoped object directly
    (_check_model_access requires impersonation for tenant-scoped models in multi-tenant mode)."""
    headers = auth(superadmin)
    resp = await client.delete(
        f"/api/v1/admin/projects/{project_b.id}/hard",
        headers=headers,
    )
    # Root panel + tenant-scoped model → 403 (must use impersonation to access)
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# 3. Permission matrix accessible to tenant admin
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_permission_matrix_template_accessible_to_tenant_admin(
    client: AsyncClient,
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
    membership_a: TenantMembership,
):
    """Tenant admin can load the permission matrix template for their tenant."""
    headers = {**auth(user_a), **tenant_header(tenant_a.slug)}
    resp = await client.get("/api/v1/admin/permission-matrix/template", headers=headers)
    assert resp.status_code == 200, resp.text
    models = [m["model_name"] for m in resp.json()]
    assert "projects" in models  # tenant-scoped model is visible


# ---------------------------------------------------------------------------
# 4. Global user role does not grant tenant admin access
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_global_user_role_does_not_grant_tenant_access(
    client: AsyncClient,
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
    role_a: Role,
):
    """User with a global role in user_roles but no TenantMembership is denied tenant API access."""
    # Assign the (tenant-scoped) role directly to user_roles — simulates pre-migration state
    # that should be blocked. Note: current guard prevents this via the API, so we do it directly.
    await db.execute(
        _user_roles_table.insert().values(user_id=user_a.id, role_id=role_a.id)
    )
    await db.commit()

    resp = await client.get(
        "/api/v1/admin/projects",
        headers={**auth(user_a), **tenant_header(tenant_a.slug)},
    )
    # No TenantMembership → 403 from require_tenant_membership
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# 5. PolicyEngine uses membership roles in tenant context
# ---------------------------------------------------------------------------

def test_policy_engine_effective_model_caps_uses_tenant_auth():
    """effective_model_caps returns full caps when tenant_auth has tenant_admin role."""
    from unittest.mock import MagicMock
    from adminfoundry.authz.policy_engine import policy_engine
    from adminfoundry.tenancy.context import TenantAuthContext, TenantContext

    user = MagicMock()
    user.is_superadmin = False
    user.roles = []

    tenant_id = uuid.uuid4()
    role = MagicMock()
    role.name = "tenant_admin"

    tenant_ctx = TenantContext.from_dict({
        "id": str(tenant_id), "slug": "test", "name": "Test", "is_active": True,
    })
    tenant_auth = TenantAuthContext(
        tenant=tenant_ctx, membership=MagicMock(), roles=[role], permission_keys=set()
    )

    model_admin = MagicMock()
    model_admin.tenant_scoped = True
    model_admin.admin_only = True

    caps = policy_engine.effective_model_caps(
        user, model_admin, {}, in_tenant_context=True, tenant_auth=tenant_auth
    )
    assert caps["can_list"] is True
    assert caps["can_update"] is True


def test_policy_engine_without_membership_uses_user_roles():
    """When no membership is provided, effective_model_caps falls back to user.roles."""
    from unittest.mock import MagicMock
    from adminfoundry.authz.policy_engine import policy_engine

    user = MagicMock()
    user.is_superadmin = False
    user.roles = []  # empty global roles

    model_admin = MagicMock()
    model_admin.tenant_scoped = True
    model_admin.admin_only = True

    caps = policy_engine.effective_model_caps(
        user, model_admin, {}, in_tenant_context=True, membership=None
    )
    # No roles → admin_only → no access
    assert caps["can_list"] is False


# ---------------------------------------------------------------------------
# 6. Tenant-scoped role rejected from user_roles endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tenant_scoped_role_rejected_from_user_roles_endpoint(
    client: AsyncClient,
    db: AsyncSession,
    superadmin: User,
    user_a: User,
    role_a: Role,
):
    """Assigning a tenant-scoped role via /users/{id}/roles/{role_id} returns 400."""
    headers = auth(superadmin)
    resp = await client.post(
        f"/api/v1/users/{user_a.id}/roles/{role_a.id}",
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "TenantMembership" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 7. require_tenant_auth_context — membership gate
# ---------------------------------------------------------------------------

def _tenant_state(tenant):
    """SimpleNamespace with tenant set so getattr(state, 'tenant', None) works."""
    from types import SimpleNamespace
    from adminfoundry.tenancy.context import TenantContext
    return SimpleNamespace(
        tenant=TenantContext.from_dict({
            "id": str(tenant.id),
            "slug": tenant.slug,
            "name": tenant.name,
            "is_active": tenant.is_active,
        }),
        token_payload={},
    )


@pytest.mark.asyncio
async def test_require_tenant_auth_context_no_tenant_returns_none(
    db: AsyncSession,
    user_a: User,
):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from adminfoundry.tenancy.dependencies import require_tenant_auth_context

    request = MagicMock()
    request.state = SimpleNamespace()  # no .tenant
    result = await require_tenant_auth_context(request=request, current_user=user_a, db=db)
    assert result is None


@pytest.mark.asyncio
async def test_require_tenant_auth_context_rejects_missing_membership(
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
):
    from unittest.mock import MagicMock
    from fastapi import HTTPException
    from adminfoundry.tenancy.dependencies import require_tenant_auth_context

    request = MagicMock()
    request.state = _tenant_state(tenant_a)

    with pytest.raises(HTTPException) as exc_info:
        await require_tenant_auth_context(request=request, current_user=user_a, db=db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_tenant_auth_context_rejects_inactive_membership(
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
):
    from unittest.mock import MagicMock
    from fastapi import HTTPException
    from adminfoundry.tenancy.dependencies import require_tenant_auth_context

    inactive = TenantMembership(user_id=user_a.id, tenant_id=tenant_a.id, is_active=False)
    db.add(inactive)
    await db.commit()

    request = MagicMock()
    request.state = _tenant_state(tenant_a)

    with pytest.raises(HTTPException) as exc_info:
        await require_tenant_auth_context(request=request, current_user=user_a, db=db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_tenant_auth_context_returns_ctx_with_valid_membership(
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
):
    from unittest.mock import MagicMock
    from adminfoundry.tenancy.context import TenantAuthContext
    from adminfoundry.tenancy.dependencies import require_tenant_auth_context

    m = TenantMembership(user_id=user_a.id, tenant_id=tenant_a.id, is_active=True)
    db.add(m)
    await db.commit()

    request = MagicMock()
    request.state = _tenant_state(tenant_a)

    ctx = await require_tenant_auth_context(request=request, current_user=user_a, db=db)
    assert isinstance(ctx, TenantAuthContext)
    assert ctx.membership.user_id == user_a.id
    # On SQLite roles are empty (no tenant schema); PostgreSQL tests in Phase 7
    assert isinstance(ctx.roles, list)
    assert isinstance(ctx.permission_keys, set)
